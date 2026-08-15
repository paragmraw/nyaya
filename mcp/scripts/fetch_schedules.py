"""One-off helper to fetch the 12 Constitution schedules from constitutionofindia.net
(CLPR) and write them as data/manual/schedules/{N}_{title}.md.

Run from the mcp/ directory:
    python scripts/fetch_schedules.py

The site renders three blocks per schedule: the current consolidated text, a
"VERSION 1/2" historical block, and a "SUMMARY" block. We keep only the current
text — everything between the schedule heading and the first "VERSION" / "SUMMARY"
marker. Government-edict text is public domain (Copyright Act s.52(1)(q)).

Schedules 1, 2, 5, 7 span multiple sub-pages; we fetch and concatenate the parts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

OUT_DIR = Path("data/manual/schedules")
BASE = "https://www.constitutionofindia.net/schedules"

# (number, filename_title, list of sub-page slugs). The slugs are the URL path
# segments after /schedules/. Order matters for multi-part schedules. Slugs
# were discovered via the WordPress API: /wp-json/wp/v2/schedules?per_page=100
SCHEDULES: list[tuple[int, str, list[str]]] = [
    (1, "States and Union Territories", ["i-the-states", "ii-the-union-territories"]),
    (2, "Emoluments Allowances and Privileges", [
        "a-provisions-as-to-the-president-and-the-governors-of-states",
        "c-provisions-as-to-the-speaker-and-the-deputy-speaker-of-the-house-of-the-people-and-the-chairman-and-the-deputy-chairman-of-the-council-of-states-and-the-speaker-and-the-deputy-speaker-of-the-legisl",
        "d-provisions-as-to-the-judges-of-the-supreme-court-and-of-the-high-courts",
        "e-provisions-as-to-the-comptroller-and-auditor-general-of-india",
    ]),
    (3, "Oaths and Affirmations", ["forms-of-oaths-or-affirmations"]),
    (4, "Allocation of Seats in the Council of States", ["allocation-of-seats-in-the-council-of-states"]),
    (5, "Administration of Scheduled Areas", [
        "part-a-provisions-as-to-the-administration-and-control-of-scheduled-areas-and-scheduled-tribes",
        "part-b-administration-and-control-of-scheduled-areas-and-scheduled-tribes",
        "part-c-scheduled-areas",
        "part-d-amendment-of-the-schedule",
    ]),
    (6, "Administration of Tribal Areas", [
        "provisions-as-to-the-administration-of-tribal-areas-in-the-states-of-assam-meghalaya-tripura-and-mizoram",
    ]),
    (7, "Union State and Concurrent Lists", [
        "list-i-union-list",
        "list-ii-state-list",
        "list-iii-concurrent-list",
    ]),
    (8, "Languages", ["languages"]),
    (9, "Validation of Certain Acts and Regulations", ["ninth-schedule"]),
    (10, "Anti Defection", ["provisions-as-to-disqualification-on-ground-of-defection"]),
    (11, "Panchayats", ["eleventh-schedule"]),
    (12, "Municipalities", ["twelfth-schedule"]),
]

# Markers that end the current-text block on each page.
END_MARKERS = re.compile(r"^\s*(VERSION\s+\d+|SUMMARY)\b", re.IGNORECASE)


def _fetch(client: httpx.Client, slug: str) -> str:
    url = f"{BASE}/{slug}/"
    r = client.get(url, headers={"User-Agent": "nyaya-fetch/0.1"})
    r.raise_for_status()
    return r.text


def _extract_current_block(html: str) -> str:
    """Pull the current consolidated schedule text from the page HTML.

    The HTML has the current text followed by VERSION 1 / VERSION 2 / SUMMARY
    blocks. We extract visible text and trim at the first VERSION/SUMMARY marker.
    """
    # Strip script/style blocks first so their contents don't pollute the text.
    # Use regex that also matches malformed closing tags like </script foo="bar">
    html = re.sub(r"(?i)<script[^>]*>.*?</script\s*[^>]*>", "", html, flags=re.DOTALL)
    html = re.sub(r"(?i)<style[^>]*>.*?</style\s*[^>]*>", "", html, flags=re.DOTALL)
    # Convert <br>, <p>, </li>, </tr>, </h*>, </td> to newlines for structure.
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(p|li|tr|h[1-6]|td|div)>", "\n", html, flags=re.IGNORECASE)
    # Drop all remaining tags.
    text = re.sub(r"<[^>]+>", "", html)
    # Decode common HTML entities.
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&quot;", '"').replace("&#8217;", "'").replace("&#8211;", "-")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&hellip;", "…"))
    # Collapse excessive blank lines.
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        if ln.strip() == "":
            if not blank:
                out.append("")
                blank = True
            continue
        blank = False
        out.append(ln)
    text = "\n".join(out).strip()

    # Trim everything from the first VERSION/SUMMARY marker onward.
    trimmed: list[str] = []
    for ln in text.splitlines():
        if END_MARKERS.match(ln):
            break
        trimmed.append(ln)
    return "\n".join(trimmed).strip()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(follow_redirects=True, timeout=60.0)
    # Heading markers that begin each schedule's actual content. The CLPR pages
    # use inconsistent headings ("Eighth Schedule" vs "Eight Schedule", or just
    # the topic title). We try each in order and cut at the first hit.
    heading_patterns = {
        1: ["First Schedule"],
        2: ["Second Schedule"],
        3: ["Third Schedule", "Forms of Oaths"],
        4: ["Fourth Schedule", "Allocation of seats"],
        5: ["Fifth Schedule"],
        6: ["Sixth Schedule"],
        7: ["Seventh Schedule", "List I: Union"],
        8: ["Eighth Schedule", "Eight Schedule", "Languages"],
        9: ["Ninth Schedule"],
        10: ["Tenth Schedule"],
        11: ["Eleventh Schedule", "Eleventh"],
        12: ["Twelfth Schedule", "Twelfth"],
    }
    for num, title, slugs in SCHEDULES:
        parts: list[str] = []
        for slug in slugs:
            print(f"  fetching {slug}…")
            html = _fetch(client, slug)
            part = _extract_current_block(html)
            if not part:
                print(f"    ! no text extracted from {slug}", file=sys.stderr)
            parts.append(part)
        body = "\n\n".join(p for p in parts if p)
        # Trim everything before the actual schedule heading. The site nav
        # and breadcrumb appear first; the schedule body starts at one of the
        # heading markers. Use the first hit across all concatenated parts.
        markers = heading_patterns[num]
        cut = -1
        for marker in markers:
            idx = body.find(marker)
            if idx >= 0:
                cut = idx
                break
        if cut >= 0:
            body = body[cut:]
        # Also strip the trailing site chrome (footer/copyright). The schedule
        # text ends well before the footer; cut at the first footer marker.
        for footer in ["We are a not-for-profit", "© 2026", "About Us\nEvents",
                       "SUPPORT US", "Privacy Policy"]:
            idx = body.find(footer)
            if idx >= 0:
                body = body[:idx]
                break
        # Normalize whitespace: collapse runs of spaces/tabs and blank lines.
        lines = [re.sub(r"[ \t]+", " ", ln).rstrip() for ln in body.splitlines()]
        out: list[str] = []
        blank = False
        for ln in lines:
            if ln.strip() == "":
                if not blank:
                    out.append("")
                    blank = True
                continue
            blank = False
            out.append(ln.strip())
        body = "\n".join(out).strip()
        fname = f"{num}_{title.replace(' ', '_')}.md"
        (OUT_DIR / fname).write_text(body, encoding="utf-8")
        print(f"  ✓ Schedule {num}: {len(body)} chars -> {fname}")
    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
