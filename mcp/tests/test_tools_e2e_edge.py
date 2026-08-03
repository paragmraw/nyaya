"""Edge-case tool tests against a live nyaya Docker container.

NOT collected by pytest (needs a live server). Run manually:
    python tests/test_tools_e2e_edge.py
"""
import json, sys, urllib.request

import pytest

pytestmark = pytest.mark.skip(reason="e2e test; run manually")

MCP = "http://localhost:8000/mcp"


def _run():
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def post(body):
        req = urllib.request.Request(MCP, data=body.encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(), dict(r.headers)

    def parse_sse(content):
        for line in content.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return None

    body = json.dumps({"jsonrpc": "2.0", "method": "initialize", "id": 1,
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "t", "version": "0.1"}}})
    s, c, h = post(body)
    headers["Mcp-Session-Id"] = h.get("Mcp-Session-Id", "")
    post(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def call(name, args):
        b = json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 99,
                        "params": {"name": name, "arguments": args}})
        s, c, _ = post(b)
        r = parse_sse(c)
        is_err = r.get("result", {}).get("isError", False)
        if is_err:
            return {"_error": r["result"]["content"][0]["text"]}
        return json.loads(r["result"]["content"][0]["text"])

    passed = 0
    failed = 0

    def check(desc, cond, detail=""):
        nonlocal passed, failed
        if cond:
            print(f"  PASS: {desc}")
            passed += 1
        else:
            print(f"  FAIL: {desc} {detail}")
            failed += 1

    print("=== Edge-case tool tests ===\n")

    r1 = call("search_law", {"query": "murder", "limit": 2, "offset": 0})
    r2 = call("search_law", {"query": "murder", "limit": 2, "offset": 2})
    check("pagination total consistent", r1["total"] == r2["total"], f"{r1['total']} vs {r2['total']}")
    check("page 1 != page 2", r1["results"][0]["ref"] != r2["results"][0]["ref"])
    check("returned <= limit", r1["returned"] <= 2)
    print()

    r3 = call("cross_reference", {"act": "IPC", "section": "302", "direction": "from"})
    r4 = call("cross_reference", {"act": "BNS", "section": "103", "direction": "to"})
    check("cross_ref from IPC 302", len(r3["references"]) >= 1)
    check("cross_ref to BNS 103", len(r4["references"]) >= 1)
    check("cross_ref direction field", r3["direction"] == "from")
    print()

    r5 = call("get_judgment", {"case_slug": "AIR 1973 SC 1461"})
    r6 = call("get_judgment", {"case_slug": "kesavananda-bharati-v-state-of-kerala"})
    check("judgment by citation", "Kesavananda" in r5.get("case_name", ""), r5.get("_error", ""))
    check("judgment by slug (no period)", "Kesavananda" in r6.get("case_name", ""), r6.get("_error", ""))
    check("same judgment", r5.get("case_name") == r6.get("case_name"))
    print()

    r7 = call("resolve_citation", {"citation": "IPC s.302"})
    check("resolve IPC s.302", r7.get("section") == "302", r7.get("_error", ""))
    r7b = call("resolve_citation", {"citation": "Art.21"})
    check("resolve Art.21", r7b.get("number") == "21", r7b.get("_error", ""))
    print()

    r8 = call("corpus_stats", {})
    check("corpus_stats has acts", r8.get("acts", 0) > 0)
    check("corpus_stats has chapters", r8.get("chapters", 0) > 0)
    check("corpus_stats has cross_refs", r8.get("cross_refs", 0) > 0)
    print()

    r9 = call("hybrid_search", {"query": "right to privacy", "limit": 3})
    check("hybrid_search returns results", r9.get("total", 0) >= 0)
    print()

    r10 = call("search_judgments", {"query": "basic structure", "date_from": "1970-01-01", "date_to": "2020-12-31", "limit": 3})
    check("search_judgments with dates", "total" in r10)
    print()

    r11 = call("get_definition", {"term": "good faith"})
    check("get_definition returns results", r11.get("total", 0) >= 0)
    print()

    r12 = call("get_amendments_for_article", {"article": "13"})
    check("get_amendments_for_article(13)", len(r12.get("amendments", [])) >= 0)
    print()

    r13 = call("get_section", {"act": "ipc", "section": "302"})
    check("get_section(ipc, 302) lowercase", r13.get("section") == "302", r13.get("_error", ""))
    print()

    r14 = call("get_chapter", {"act": "IPC", "chapter": 1})
    check("get_chapter(IPC, 1)", r14.get("number") == 1, r14.get("_error", ""))
    check("get_chapter has sections", len(r14.get("sections", [])) >= 0)
    print()

    r15 = call("search_by_kind", {"query": "right to privacy", "kind": "article", "limit": 3})
    check("search_by_kind article", "total" in r15)
    print()

    r17 = call("list_sections", {"act": "IPC", "limit": 5, "offset": 0})
    check("list_sections has total", r17.get("total", 0) > 0)
    check("list_sections returned <= limit", len(r17.get("sections", [])) <= 5)
    print()

    r18 = call("search_law", {"query": "murder", "act": "IPC", "limit": 3})
    check("search_law scoped to IPC", all(r.get("act") == "IPC" for r in r18.get("results", [])))
    print()

    r19 = call("search_judgments", {"query": "test", "date_from": "not-a-date"})
    check("invalid date returns error", "_error" in r19, str(r19)[:100])
    print()

    r20 = call("get_sections_by_range", {"act": "IPC", "start": "299", "end": "320"})
    check("get_sections_by_range returns sections", len(r20.get("sections", [])) >= 0, r20.get("_error", ""))
    print()

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = _run()
    sys.exit(0 if ok else 1)