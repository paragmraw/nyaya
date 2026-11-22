"""Tests for pure-import ``mcp_app.py`` and ``cli.py`` dual-transport stdio/HTTP entrypoints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from nyaya import cli
from tests.test_tools_contract import EXPECTED_12_TOOLS


def test_mcp_app_import_and_create_mcp_never_calls_get_settings():
    """Importing nyaya.mcp_app and calling create_mcp() must not invoke get_settings()."""
    code = (
        "import nyaya.config as cfg\n"
        "def _forbid(): raise RuntimeError('get_settings called at import/create_mcp time!')\n"
        "cfg.get_settings = _forbid\n"
        "from nyaya.mcp_app import create_mcp\n"
        "mcp = create_mcp()\n"
        "print('OK')\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DATABASE_URL", "NVIDIA_API_KEY", "NYAYA_BUCKET_ENDPOINT"}
    }
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    assert res.stdout.strip() == "OK"


def test_stdio_jsonrpc_handshake_and_tools_list_zero_env_zero_stdout_noise(tmp_path: Path):
    """Subprocess stdio JSON-RPC initialize + tools/list works with 0 secrets and 0 non-JSON stdout bytes."""
    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-stdio", "version": "2.0.0"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        ]
    ) + "\n"

    clean_env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("NYAYA_") and k not in {"DATABASE_URL", "NVIDIA_API_KEY", "REDIS_URL"}
    }

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "nyaya.cli",
            "--transport",
            "stdio",
            "--data-dir",
            str(tmp_path),
            "--no-download",
            "--offline-only",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=clean_env,
        input=requests,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"stdio failed (rc={proc.returncode}): stderr={proc.stderr!r}"

    raw_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(raw_lines) >= 2, f"Expected >=2 JSON-RPC responses on stdout, got: {proc.stdout!r}"

    parsed_lines = []
    for line in raw_lines:
        # Every single byte on stdout MUST be valid JSON-RPC 2.0
        msg = json.loads(line)
        assert msg.get("jsonrpc") == "2.0"
        parsed_lines.append(msg)

    init_resp = next(m for m in parsed_lines if m.get("id") == 1)
    assert "result" in init_resp

    tools_resp = next(m for m in parsed_lines if m.get("id") == 2)
    tools = tools_resp["result"]["tools"]
    assert len(tools) == 12
    assert {t["name"] for t in tools} == EXPECTED_12_TOOLS


def test_cli_sync_subcommand_builds_local_core(tmp_path: Path):
    """`nyaya-mcp sync --data-dir <tmp>` builds/ensures nyaya-core.sqlite offline."""
    rc = cli.main(
        [
            "sync",
            "--pack",
            "core",
            "--data-dir",
            str(tmp_path),
            "--offline-only",
        ]
    )
    assert rc == 0
    assert (tmp_path / "nyaya-core.sqlite").is_file()
