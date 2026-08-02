"""One-off helper to fetch full judgment text for the 5 landmark cases from
indiankanoon.org and rewrite data/manual/judgments.yaml with the real bodies.

Run from the mcp/ directory:
    python scripts/fetch_judgments.py

The judgment body lives inside <div class="judgments">...</div>. We extract
that, strip the citation/cover block, and keep the case title + bench + body.
Public domain in India (Copyright Act s.52(1)(q) — government edicts).
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import yaml

URLS = {
    "Kesavananda Bharati v. State of Kerala": "https://indiankanoon.org/doc/257876/",
    "Maneka Gandhi v. Union of India": "https://indiankanoon.org/doc/1766147/",
    "K.S. Puttaswamy v. Union of India": "https://indiankanoon.org/doc/91938676/",
    "Mohammed Ahmed Khan v. Shah Bano Begum": "https://indiankanoon.org/doc/823221/",
    "Navtej Singh Johar v. Union of India": "https://indiankanoon.org/doc/168671544/",
}


def _fetch(client: httpx.Client, url: str) -> str:
    r = client.get(url, headers={"User-Agent": "nyaya-fetch/0.1 (+research)"})
    r.raise_for_status()
    return r.text


def _extract_body(html: str) -> str:
    # Isolate the <div class="judgments">…</div> block.
    m = re.search(r'<div class="judgments"[^>]*>', html)
    if not m:
        return ""
    start = m.end()
    # The judgments div ends before the homepage-footer / docoptions. Find the
    # closing by depth-tracking divs is heavy; instead, cut at the next sibling
    # section that begins with <div class="docoptions" or <footer.
    end_markers = [r'<div class="docoptions"', r'<div class="homepage-footer"',
                   r'<footer', r'<div class="action-button"']
    end = len(html)
    for marker in end_markers:
        idx = html.find(marker, start)
        if 0 < idx < end:
            end = idx
    body_html = html[start:end]

    # Strip scripts/styles.
    body_html = re.sub(r"<script[^>]*>.*?</script>", "", body_html, flags=re.DOTALL | re.IGNORECASE)
    body_html = re.sub(r"<style[^>]*>.*?</style>", "", body_html, flags=re.DOTALL | re.IGNORECASE)

    # Convert to text: preserve paragraph breaks.
    body_html = re.sub(r"<br\s*/?>", "\n", body_html, flags=re.IGNORECASE)
    body_html = re.sub(r"</(p|div|li|h[1-6])>", "\n", body_html, flags=re.IGNORECASE)
    body_html = re.sub(r"<[^>]+>", "", body_html)
    body_html = (body_html.replace("&amp;", "&").replace("&nbsp;", " ")
                 .replace("&quot;", '"').replace("&#8217;", "'").replace("&#8211;", "-")
                 .replace("&lt;", "<").replace("&gt;", ">").replace("&hellip;", "…")
                 .replace("&#8220;", '"').replace("&#8221;", '"'))
    # Normalize whitespace: tabs to single space, collapse blank lines.
    lines = [ln.replace("\t", " ").rstrip() for ln in body_html.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        # Collapse runs of internal spaces (the PDF extraction leaves odd spacing).
        ln = re.sub(r" {2,}", " ", ln).strip()
        if ln == "":
            if not blank:
                out.append("")
                blank = True
            continue
        blank = False
        out.append(ln)
    return "\n".join(out).strip()


def main() -> None:
    yaml_path = Path("data/manual/judgments.yaml")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    judgments = data.get("judgments", []) if isinstance(data, dict) else data

    client = httpx.Client(follow_redirects=True, timeout=120.0)
    for j in judgments:
        case_name = j["case_name"]
        url = URLS.get(case_name)
        if not url:
            print(f"  ! No URL for {case_name!r}; leaving placeholder.")
            continue
        print(f"→ Fetching {case_name}…")
        html = _fetch(client, url)
        body = _extract_body(html)
        if not body:
            print(f"  ! Could not extract body from {url}; leaving placeholder.")
            continue
        j["text"] = body
        print(f"  ✓ {len(body)} chars.")
    client.close()

    # Write back, preserving the original structure (dict with 'judgments' key
    # if it was a dict, else a bare list). Use block scalars for readability.
    out = {"judgments": judgments} if isinstance(data, dict) else judgments
    yaml_path.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True,
                                        default_flow_style=False, width=10000),
                         encoding="utf-8")
    print(f"✓ Wrote {yaml_path} ({yaml_path.stat().st_size} bytes).")


if __name__ == "__main__":
    main()