"""Opt-in, permissively-licensed OCR adapters for scanned Indian legal PDFs.

Supports only permissive Apache-2.0 OCR backends:
- ``TesseractOcrEngine`` via system ``tesseract`` CLI (Apache-2.0)
- ``RapidOcrEngine`` via lazy import of ``rapidocr_onnxruntime`` (Apache-2.0)
"""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OcrEngine(Protocol):
    """Protocol for opt-in page-level OCR backends."""

    name: str

    def is_available(self) -> bool: ...

    def extract_text(self, image_bytes: bytes, *, lang: str = "eng") -> str: ...


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack("!I", len(data)) + body + struct.pack("!I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_raw_pixels_to_png(
    raw_bytes: bytes | memoryview,
    *,
    width: int,
    height: int,
    channels: int,
    stride: int | None = None,
) -> bytes:
    """Encode an 8-bit grayscale/RGB/RGBA pixel buffer to PNG bytes using stdlib ``zlib``."""
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")
    color_type_map = {1: 0, 3: 2, 4: 6}
    color_type = color_type_map.get(channels)
    if color_type is None:
        raise ValueError(f"Unsupported channel count {channels}; expected 1, 3, or 4.")

    row_bytes = width * channels
    eff_stride = stride if stride is not None else row_bytes
    view = memoryview(raw_bytes)
    scanlines = bytearray()
    for y in range(height):
        start = y * eff_stride
        scanlines.append(0)  # PNG filter type 0 (None)
        scanlines.extend(view[start : start + row_bytes])

    ihdr = struct.pack("!IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    idat = zlib.compress(bytes(scanlines), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def render_pdf_page_to_png(page: Any, *, scale: float = 2.0) -> bytes:
    """Render a ``pypdfium2`` ``PdfPage`` into RGB/RGBA PNG bytes without Pillow."""
    bitmap = page.render(scale=scale, rev_byteorder=True)
    try:
        return encode_raw_pixels_to_png(
            bitmap.buffer,
            width=int(bitmap.width),
            height=int(bitmap.height),
            channels=int(bitmap.n_channels),
            stride=int(bitmap.stride),
        )
    finally:
        if hasattr(bitmap, "close"):
            bitmap.close()


class TesseractOcrEngine:
    """Apache-2.0 Tesseract CLI OCR adapter invoked via ``subprocess``."""

    name: str = "tesseract"

    def __init__(
        self,
        *,
        tesseract_cmd: str = "tesseract",
        lang: str = "eng",
        timeout_s: float = 30.0,
    ) -> None:
        self.tesseract_cmd = tesseract_cmd
        self.lang = lang
        self.timeout_s = float(timeout_s)

    def is_available(self) -> bool:
        return shutil.which(self.tesseract_cmd) is not None

    def extract_text(self, image_bytes: bytes, *, lang: str = "eng") -> str:
        exe = shutil.which(self.tesseract_cmd)
        if exe is None:
            raise RuntimeError(f"Tesseract CLI {self.tesseract_cmd!r} is not installed.")
        eff_lang = lang or self.lang
        with tempfile.TemporaryDirectory(prefix="nyaya_ocr_") as tmpdir:
            img_path = Path(tmpdir) / "page.png"
            img_path.write_bytes(image_bytes)
            proc = subprocess.run(
                [exe, str(img_path), "stdout", "-l", eff_lang],
                capture_output=True,
                check=False,
                timeout=self.timeout_s,
            )
        if proc.returncode != 0:
            err = (
                proc.stderr.decode("utf-8", errors="replace")
                if isinstance(proc.stderr, (bytes, bytearray))
                else str(proc.stderr or "")
            )
            raise RuntimeError(f"tesseract exited with code {proc.returncode}: {err.strip()}")
        out = (
            proc.stdout.decode("utf-8", errors="replace")
            if isinstance(proc.stdout, (bytes, bytearray))
            else str(proc.stdout or "")
        )
        return out.strip()


class RapidOcrEngine:
    """Apache-2.0 ``rapidocr_onnxruntime`` adapter with lazy module import."""

    name: str = "rapidocr"

    def __init__(self, *, engine_factory: Any = None) -> None:
        self._engine_factory = engine_factory
        self._engine: Any = None

    def is_available(self) -> bool:
        if self._engine is not None or self._engine_factory is not None:
            return True
        if "rapidocr_onnxruntime" in sys.modules:
            return True
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None

    def _get_engine(self) -> Any:
        if self._engine is None:
            if self._engine_factory is not None:
                self._engine = self._engine_factory()
            else:
                mod = importlib.import_module("rapidocr_onnxruntime")
                self._engine = mod.RapidOCR()
        return self._engine

    def extract_text(self, image_bytes: bytes, *, lang: str = "eng") -> str:
        del lang  # RapidOCR uses its configured ONNX recognition model
        engine = self._get_engine()
        output = engine(image_bytes)
        items = output[0] if isinstance(output, tuple) else output
        if not items:
            return ""
        lines: list[str] = []
        for item in items:
            if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]:
                lines.append(str(item[1]).strip())
        return "\n".join(line for line in lines if line).strip()


def get_default_ocr_engine() -> OcrEngine | None:
    """Return the first available permissive OCR engine, or ``None`` if none installed."""
    for candidate in (RapidOcrEngine(), TesseractOcrEngine()):
        if candidate.is_available():
            return candidate
    return None
