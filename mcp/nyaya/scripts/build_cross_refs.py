"""Build cross-references between sections and acts.

Two sources of cross-references:
  1. A manual IPC↔BNS mapping file (data/manual/ipc_bns_map.yaml).
  2. Regex over all section text for phrases like "section 65 of the Indian
     Evidence Act" or "Article 21 of the Constitution".
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .db import IngestDB

IPC_BNS_MAP_FILE = Path("data/manual/ipc_bns_map.yaml")

CROSS_REF_RE = re.compile(
    r"(?:section|s\.?)\s*(?P<num>\d+[A-Z]?)\s+of\s+(?:the\s+)?(?P<act>[A-Z][^.,;]+?)(?:\s+Act)?(?:[.,;]|$)",
    re.IGNORECASE,
)
ARTICLE_REF_RE = re.compile(
    r"article\s*(?P<num>\d+[A-Z]?)\s+of\s+(?:the\s+)?Constitution",
    re.IGNORECASE,
)

ACT_ALIASES = {
    "indian penal code": "IPC",
    "code of criminal procedure": "CrPC",
    "code of civil procedure": "CPC",
    "indian evidence act": "EvidenceAct",
    "bharatiya nyaya sanhita": "BNS",
    "bharatiya nagarik suraksha sanhita": "BNSS",
    "bharatiya sakshya adhiniyam": "BSA",
    "companies act": "Companies",
    "information technology act": "ITAct",
    "arbitration and conciliation act": "Arbitration",
    "consumer protection act": "ConsumerProtection",
}


def _alias_to_short(name: str) -> str | None:
    low = name.lower().strip()
    for needle, short in ACT_ALIASES.items():
        if needle in low:
            return short
    return None


def _load_manual_map(db: IngestDB) -> int:
    if not IPC_BNS_MAP_FILE.is_file():
        print(f"  ! No IPC↔BNS map at {IPC_BNS_MAP_FILE}; skipping manual mapping.")
        return 0
    data = yaml.safe_load(IPC_BNS_MAP_FILE.read_text(encoding="utf-8"))
    mappings = data.get("mappings", []) if isinstance(data, dict) else data
    n = 0
    for m in mappings:
        ipc = str(m["ipc"])
        bns = str(m["bns"])
        kind = m.get("kind", "corresponds_to")
        db.add_cross_ref(from_act="IPC", from_section=ipc, to_act="BNS", to_section=bns, kind=kind)
        db.add_cross_ref(from_act="BNS", from_section=bns, to_act="IPC", to_section=ipc, kind="replaced_by")
        n += 2
    return n


def _scan_text_refs(db: IngestDB) -> int:
    rows = db.fetch_all("select a.short_name as act, s.number, s.text from sections s join acts a on a.id = s.act_id")
    n = 0
    for row in rows:
        src_act, src_section, text = row["act"], row["number"], row["text"]
        for m in CROSS_REF_RE.finditer(text):
            target_act = _alias_to_short(m.group("act"))
            if not target_act or target_act == src_act:
                continue
            db.add_cross_ref(
                from_act=src_act,
                from_section=src_section,
                to_act=target_act,
                to_section=m.group("num"),
                kind="references",
            )
            n += 1
        for m in ARTICLE_REF_RE.finditer(text):
            db.add_cross_ref(
                from_act=src_act,
                from_section=src_section,
                to_act="Constitution",
                to_section=m.group("num"),
                kind="references",
            )
            n += 1
    return n


def build_cross_refs(db: IngestDB) -> None:
    print("→ Loading manual IPC↔BNS mapping…")
    n1 = _load_manual_map(db)
    print(f"  ✓ {n1} manual cross-refs added.")
    print("→ Scanning section text for inline references…")
    n2 = _scan_text_refs(db)
    print(f"  ✓ {n2} text-derived cross-refs added.")
    db.commit()
    print(f"✓ Cross-reference build complete ({n1 + n2} total).")


def main() -> None:
    with IngestDB() as db:
        build_cross_refs(db)


if __name__ == "__main__":
    main()