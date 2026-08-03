"""Rigorous end-to-end tool testing against a running nyaya MCP server.

NOT collected by pytest (needs a live server). Run manually:
    python tests/test_tools_e2e.py [base_url]
"""

from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
MCP = f"{BASE}/mcp"
HEALTH = f"{BASE}/health"


import pytest

# Skip this entire module from pytest collection — it's a manual e2e test
# that needs a running Docker container.
pytestmark = pytest.mark.skip(reason="e2e test; run manually with: python tests/test_tools_e2e.py")


def _post(body: str, headers: dict[str, str]) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(MCP, data=body.encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode()
            return resp.status, content, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def _parse_sse(content: str) -> dict | None:
    """Parse an SSE 'event: message\ndata: {...}' response."""
    for line in content.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return None


def initialize() -> dict[str, str]:
    """Perform MCP initialize handshake, return headers with session ID."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    body = json.dumps({
        "jsonrpc": "2.0", "method": "initialize", "id": 1,
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "e2e-test", "version": "0.1"}}
    })
    status, content, resp_headers = _post(body, headers)
    if status != 200:
        print(f"FAIL: initialize returned {status}: {content[:200]}")
        sys.exit(1)
    session_id = resp_headers.get("Mcp-Session-Id", "")
    headers["Mcp-Session-Id"] = session_id
    # Send initialized notification
    notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    _post(notif, headers)
    return headers


def call_tool(headers: dict[str, str], name: str, args: dict) -> dict | None:
    """Call a tool and return the parsed JSON-RPC result."""
    body = json.dumps({
        "jsonrpc": "2.0", "method": "tools/call", "id": 99,
        "params": {"name": name, "arguments": args}
    })
    status, content, _ = _post(body, headers)
    if status != 200:
        return {"error": f"HTTP {status}: {content[:300]}"}
    parsed = _parse_sse(content)
    if parsed is None:
        return {"error": f"Could not parse SSE: {content[:300]}"}
    return parsed


def main() -> None:
    # 1. Health check
    try:
        with urllib.request.urlopen(HEALTH, timeout=5) as r:
            h = json.loads(r.read().decode())
        print(f"Health: {h['status']} | acts={h['counts'].get('acts')} sections={h['counts'].get('sections')}")
        if h["status"] != "healthy":
            print("WARNING: server is degraded, tool calls may fail.")
    except Exception as e:
        print(f"FAIL: health check: {e}")
        sys.exit(1)

    # 2. Initialize
    headers = initialize()
    print(f"Initialized (session={headers.get('Mcp-Session-Id', 'none')[:20]}…)")

    # 3. Define tool calls with realistic args
    tool_calls: list[tuple[str, dict]] = [
        ("list_acts", {}),
        ("list_chapters", {"act": "IPC"}),
        ("list_sections", {"act": "IPC", "limit": 5}),
        ("list_articles", {"limit": 5}),
        ("list_judgments", {"limit": 5}),
        ("list_schedules", {}),
        ("list_amendments", {"year_from": 1950, "year_to": 1960}),
        ("get_section", {"act": "IPC", "section": "302"}),
        ("get_article", {"article": "21"}),
        ("get_judgment", {"case_slug": "AIR 1973 SC 1461"}),
        ("get_schedule", {"number": 1}),
        ("get_amendment", {"number": 1}),
        ("search_law", {"query": "murder", "limit": 3}),
        ("search_judgments", {"query": "basic structure", "limit": 3}),
        ("search_by_kind", {"query": "right to privacy", "kind": "article", "limit": 3}),
        ("cross_reference", {"act": "IPC", "section": "302"}),
        ("get_sections_by_range", {"act": "IPC", "start": "299", "end": "310"}),
        ("get_chapter", {"act": "IPC", "chapter": 1}),
        ("get_amendments_for_article", {"article": "13"}),
        ("get_definition", {"term": "good faith"}),
        ("corpus_stats", {}),
        ("resolve_citation", {"citation": "IPC s.302"}),
        ("semantic_query", {"query": "right to privacy", "limit": 3}),
        ("hybrid_search", {"query": "murder", "limit": 3}),
    ]

    passed = 0
    failed = 0
    for name, args in tool_calls:
        result = call_tool(headers, name, args)
        if result is None:
            print(f"  FAIL {name}: no response")
            failed += 1
            continue
        if "error" in result:
            print(f"  FAIL {name}: {result['error']}")
            failed += 1
            continue
        # Check for MCP-level error in the result
        if "error" in result.get("result", {}):
            err = result["result"]["error"]
            msg = err.get("message", str(err))[:120]
            print(f"  FAIL {name}: MCP error: {msg}")
            failed += 1
            continue
        # Check for is_error in the tool result content
        content = result.get("result", {}).get("content", [])
        if content and isinstance(content, list) and content[0].get("type") == "text":
            try:
                tool_result = json.loads(content[0]["text"])
                if isinstance(tool_result, dict) and tool_result.get("isError"):
                    print(f"  FAIL {name}: tool returned isError=True")
                    failed += 1
                    continue
            except (json.JSONDecodeError, KeyError):
                pass
        # Success
        # Print a brief summary of what was returned
        text_content = content[0].get("text", "") if content else ""
        try:
            parsed_text = json.loads(text_content)
            if isinstance(parsed_text, dict):
                keys = list(parsed_text.keys())[:5]
                print(f"  OK   {name}: keys={keys}")
            elif isinstance(parsed_text, list):
                print(f"  OK   {name}: list[{len(parsed_text)}]")
            else:
                print(f"  OK   {name}: {type(parsed_text).__name__}")
        except json.JSONDecodeError:
            print(f"  OK   {name}: (text)")
        passed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {len(tool_calls)} total")
    return failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)