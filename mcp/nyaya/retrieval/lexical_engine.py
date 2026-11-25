"""Phase-1 pure lexical, metadata, and citation-graph RetrievalEngine."""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import date as Date
from typing import Any

from ..citations import court_code_variants, court_display_name
from ..config import Settings
from ..exceptions import CommercialLicenseBlocked, QueryTooBroad, StorageUnavailable
from ..models import (
    CourtTier,
    JudgmentCard,
    JudgmentSearchQuery,
    JudgmentSearchResponse,
    SourceProvenance,
    StatuteHit,
    StatuteSearchQuery,
    StatuteSearchResponse,
    TextAvailability,
)
from ..storage.duckdb_store import DuckDBStore
from ..storage.lance_store import LanceStore
from ..storage.memory_backend import InMemoryCorpusBackend
from ..storage.protocols import CorpusBackend
from ..storage.sqlite_store import (
    SqliteStore,
    _distinct_lower,
    _extract_fts_tokens,
    _multitoken_min_required,
    _token_matches_boundary,
)
from .query_analyzer import analyze_query
from .ranking import (
    apply_definition_promotion,
    apply_exact_citation_floor,
    rank_judgment_cards,
    reciprocal_rank_fusion,
)

_STATUTE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
_DIGITS_COURT_CODE_RE = re.compile(r"^\d+[_~]\d+$")
# Python-`re` signal over a JudgmentCard's title/case-type/acts/snippet. The DuckDB title filter
# uses its own RE2 pattern (duckdb_store._ANTICIPATORY_BAIL_REGEX); the dialects differ.
_ANTICIPATORY_BAIL_SIGNAL_RE = re.compile(
    r"(?i)(?:\banticipatory\b|\bpre[- ]?arrest\b|\bapprehending\s+arrest\b|"
    r"\baba\b|\babail\b|\bcrm\s*\(\s*a\s*\)|\ba\.?\s*b\.?\s*a\.?(?:\s*[/.-]\s*\d|\s+no\.?\s*\d|\s+\d)|"
    r"\bcr\.?\s*p\.?\s*c\.?\s*(?:s\.?\s*)?438\b|"
    r"\bbnss\s*(?:s\.?\s*)?482\b|\bs(?:ec(?:tion)?)?\.?\s*(?:438|482)\b|"
    r"(?:^|[^a-z0-9])ab(?:\s*[/.-]\s*\d|\s+no\.?\s*\d|\s+\d))"
)
logger = logging.getLogger(__name__)


def _resolve_court_display_name(
    raw_court_name: Any,
    court_code: str | None,
    *,
    fallback: str,
) -> str:
    name = str(raw_court_name or "").strip()
    code = str(court_code or "").strip()
    if not name or (code and name.lower() == code.lower()) or _DIGITS_COURT_CODE_RE.match(name):
        resolved = court_display_name(code or name)
        if resolved:
            return resolved
    return name or code or fallback


def _card_search_haystack(card: JudgmentCard) -> str:
    return (
        f"{card.title} {card.court} {card.court_code or ''} "
        f"{card.bench or ''} {card.author_judge or ''} "
        f"{card.neutral_citation or ''} {' '.join(card.reporter_citations)} "
        f"{card.case_type or ''} {' '.join(card.acts_cited)} {card.decision_date or ''} "
        f"{card.disposal_nature or ''} {card.snippet or ''}"
    ).lower()


def _has_anticipatory_bail_signal(card: JudgmentCard) -> bool:
    haystack = f"{card.title} {card.case_type or ''} {' '.join(card.acts_cited)} {card.snippet or ''}"
    return bool(_ANTICIPATORY_BAIL_SIGNAL_RE.search(haystack))


def _interleave_by_court(cards: list[JudgmentCard]) -> list[JudgmentCard]:
    """Round-robin interleave cards across distinct courts, preserving within-court rank."""
    if len(cards) <= 1:
        return list(cards)
    buckets: dict[str, list[JudgmentCard]] = {}
    for card in cards:
        key = (card.court_code or card.court or "").strip().lower()
        buckets.setdefault(key, []).append(card)
    if len(buckets) <= 1:
        return list(cards)
    queues = list(buckets.values())
    out: list[JudgmentCard] = []
    idx = 0
    while len(out) < len(cards):
        progressed = False
        for q in queues:
            if idx < len(q):
                out.append(q[idx])
                progressed = True
        if not progressed:
            break
        idx += 1
    return out


def encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": int(offset)}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, default_offset: int = 0) -> int:
    if not cursor:
        return max(0, int(default_offset))
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return max(0, int(data.get("offset", 0)))
    except Exception:
        return max(0, int(default_offset))


def _lance_row_to_statute_hit(row: dict[str, Any]) -> StatuteHit:
    score = float(row.get("_score") or 1.0)
    eff_date = str(row.get("effective_date") or "2026-11-22")[:10]
    try:
        as_of = Date.fromisoformat(eff_date)
    except ValueError:
        as_of = Date(2026, 11, 22)
    return StatuteHit(
        doc_id=str(row["doc_id"]),
        act=str(row["short_name"]),
        jurisdiction=str(row.get("jurisdiction") or "Central"),
        kind=row.get("kind") or "section",
        ref=str(row["ref"]),
        title=row.get("title"),
        status=row.get("status") or "in_force",
        replaced_by=[],
        snippet=str(row.get("text") or "")[:400],
        bm25_score=round(score, 4),
        provenance=SourceProvenance(
            source_id=str(row.get("source_id") or "vaquill-state-reg"),
            source_name="Open India Law State/Regulator Pack",
            source_url=str(row.get("source_url") or "https://oss-data-in.vaquill.ai"),
            license_spdx=str(row.get("license_spdx") or "CC-BY-4.0"),
            commercial_safe=bool(row.get("commercial_safe", True)),
            terms_status=row.get("terms_status") or "cleared",
            as_of=as_of,
            content_sha256=row.get("text_sha256"),
        ),
    )


_DATTAM_SOURCES = frozenset(
    {"dattam_sc_judgments", "dattam-sc", "dattam_hc_judgments", "dattam-hc"}
)


def _canonical_judgment_key(card: JudgmentCard) -> str:
    if card.cnr and card.cnr.strip():
        return f"cnr:{card.cnr.strip().upper()}"
    if card.neutral_citation and card.neutral_citation.strip():
        return f"nc:{card.neutral_citation.strip().upper()}"
    return f"id:{card.case_id}"


def _dedup_cards(cards: list[JudgmentCard]) -> list[JudgmentCard]:
    seen: set[str] = set()
    out: list[JudgmentCard] = []
    for c in cards:
        key = _canonical_judgment_key(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _enrich_cards_from_meta(
    cards: list[JudgmentCard],
    meta_cards: list[JudgmentCard],
) -> list[JudgmentCard]:
    if not cards or not meta_cards:
        return cards
    by_case_id: dict[str, JudgmentCard] = {}
    by_cnr: dict[str, JudgmentCard] = {}
    by_nc: dict[str, JudgmentCard] = {}
    for m in meta_cards:
        if m.case_id and m.case_id not in by_case_id:
            by_case_id[m.case_id] = m
        if m.cnr and m.cnr.strip():
            by_cnr.setdefault(m.cnr.strip().upper(), m)
        if m.neutral_citation and m.neutral_citation.strip():
            by_nc.setdefault(m.neutral_citation.strip().upper(), m)

    enriched: list[JudgmentCard] = []
    for c in cards:
        match = by_case_id.get(c.case_id)
        if match is None and c.cnr and c.cnr.strip():
            match = by_cnr.get(c.cnr.strip().upper())
        if match is None and c.neutral_citation and c.neutral_citation.strip():
            match = by_nc.get(c.neutral_citation.strip().upper())
        if match is None:
            enriched.append(c)
            continue

        updates: dict[str, Any] = {}
        if c.cnr is None and match.cnr:
            updates["cnr"] = match.cnr
        if match.cited_by_count is not None and (
            c.cited_by_count is None or match.cited_by_count > c.cited_by_count
        ):
            updates["cited_by_count"] = match.cited_by_count
        if match.bench_strength is not None and (
            c.bench_strength is None or match.bench_strength > c.bench_strength
        ):
            updates["bench_strength"] = match.bench_strength
        if not c.reporter_citations and match.reporter_citations:
            updates["reporter_citations"] = list(match.reporter_citations)
        if not c.coram and match.coram:
            updates["coram"] = list(match.coram)
        enriched.append(c.model_copy(update=updates) if updates else c)
    return enriched


def _lance_row_to_judgment_card(
    row: dict[str, Any],
    *,
    tier: CourtTier,
    matched_via: str = "direct",
) -> JudgmentCard:
    reps = [str(row.get(k)) for k in ("air_citation", "scc_citation") if row.get(k)]
    d_raw = str(row.get("decision_date") or "")[:10]
    try:
        d_val = Date.fromisoformat(d_raw) if d_raw else None
    except ValueError:
        d_val = None
    court_code = str(row.get("court_code") or ("SCI" if tier == "supreme_court" else ""))
    court_name = (
        "Supreme Court of India"
        if tier == "supreme_court"
        else _resolve_court_display_name(
            row.get("court_name"),
            court_code,
            fallback="High Court",
        )
    )
    title_str = str(row.get("title") or "Untitled")
    text_str = str(row.get("text") or "")
    source_id = str(row.get("source_id") or ("dattam-sc" if tier == "supreme_court" else "dattam-hc"))
    text_avail: TextAvailability = (
        "on_demand_s3_pdf"
        if source_id in _DATTAM_SOURCES and len(text_str) <= len(title_str) + 60
        else "indexed_bm25"
    )
    return JudgmentCard(
        case_id=str(row["case_id"]),
        cnr=row.get("cnr") or None,
        neutral_citation=row.get("neutral_citation") or None,
        reporter_citations=reps,
        title=title_str,
        court=court_name,
        court_code=court_code,
        court_tier=tier,
        bench=row.get("bench"),
        decision_date=d_val,
        author_judge=row.get("author_judge"),
        bench_strength=int(row["bench_strength"]) if row.get("bench_strength") is not None else None,
        disposal_nature=row.get("disposal_nature"),
        acts_cited=[str(a) for a in (row.get("acts_cited") or [])],
        matched_via=matched_via,  # type: ignore[arg-type]
        snippet=text_str[:450],
        text_availability=text_avail,
        provenance=SourceProvenance(
            source_id=source_id,
            source_name="Dattam Labs Judgments",
            source_url=str(row.get("source_url") or "https://aws.amazon.com/opendata"),
            license_spdx=str(row.get("license_spdx") or "CC-BY-4.0"),
            commercial_safe=bool(row.get("commercial_safe", True)),
            terms_status=row.get("terms_status") or "cleared",
            as_of=d_val or Date(2026, 11, 22),
            content_sha256=row.get("text_sha256"),
        ),
    )


class LexicalRetrievalEngine:
    """Phase-1 Lexical + Metadata + Citation-Graph RetrievalEngine."""

    def __init__(
        self,
        settings: Settings,
        backend: CorpusBackend,
        *,
        lance: LanceStore | None = None,
        duckdb: DuckDBStore | None = None,
        embedder: Any = None,
        reranker: Any = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.lance = lance
        self.duckdb = duckdb
        self.embedder = embedder
        self.reranker = reranker

    def _effective_cs(self, commercial_safe_only: bool | None) -> bool:
        return (
            self.settings.commercial_safe_only
            if commercial_safe_only is None
            else bool(commercial_safe_only or self.settings.commercial_safe_only)
        )

    def search_statutes(self, q: StatuteSearchQuery) -> StatuteSearchResponse:
        cs_only = self._effective_cs(q.commercial_safe_only)
        limit = max(1, min(int(q.limit), 25))
        offset = decode_cursor(q.cursor, q.offset)
        degraded: list[str] = []

        alias_map = self.backend.get_alias_map()
        analyzed = analyze_query(
            q.query,
            act=q.act,
            include_equivalent_provisions=True,
            alias_map=alias_map,
            backend=self.backend,
        )

        # 1. Exact statutory citation matches
        exact_hits: list[StatuteHit] = []
        for s_act, s_ref in analyzed.exact_statute_refs:
            if q.act and q.act.strip().lower() not in {s_act.lower(), alias_map.get(s_act.lower(), "").lower()}:
                continue
            rec = self.backend.get_provision(
                s_act,
                s_ref,
                jurisdiction=q.jurisdiction,
                include_links=False,
                commercial_safe_only=cs_only,
            )
            if rec is not None:
                if q.status != "all" and rec.validity.status != q.status:
                    continue
                exact_hits.append(
                    StatuteHit(
                        doc_id=rec.doc_id,
                        act=rec.act,
                        jurisdiction=rec.jurisdiction,
                        kind=rec.kind,
                        ref=rec.ref,
                        title=rec.title,
                        status=rec.validity.status,
                        replaced_by=rec.validity.replaced_by,
                        snippet=rec.text[:400],
                        bm25_score=100.0,
                        provenance=rec.provenance,
                    )
                )

        # 2. SQLite FTS5
        sqlite_hits: list[StatuteHit] = []
        if isinstance(self.backend, SqliteStore):
            sqlite_hits = self.backend.search_statutes_fts(
                q.query,
                act=q.act,
                jurisdiction=q.jurisdiction,
                kind=q.kind,
                category=q.category,
                status=q.status,
                exact_phrase=(q.query_mode == "exact_phrase"),
                limit=max(50, offset + limit),
                offset=0,
                commercial_safe_only=cs_only,
            )
        elif isinstance(self.backend, InMemoryCorpusBackend):
            sqlite_hits = self.backend.search_statutes_simple(
                q.query,
                act=q.act,
                commercial_safe_only=cs_only,
                limit=max(50, offset + limit),
            )

        # 3. LanceDB State Acts & Regulators FTS
        lance_hits: list[StatuteHit] = []
        if self.lance is not None:
            try:
                l_rows = self.lance.search_state_regulators(
                    q.query,
                    act=q.act,
                    jurisdiction=q.jurisdiction,
                    category=q.category,
                    status=q.status,
                    commercial_safe_only=cs_only,
                    limit=max(25, offset + limit),
                )
                lance_hits = [_lance_row_to_statute_hit(r) for r in l_rows]
            except StorageUnavailable:
                degraded.append("lance_fts")

        # Stopword-aware multi-token word-boundary overlap guard to suppress LanceDB OR-noise
        if lance_hits and q.query_mode != "exact_phrase" and not exact_hits and not q.act:
            _, raw_content_tokens = _extract_fts_tokens(q.query)
            content_tokens = _distinct_lower(raw_content_tokens)
            if len(content_tokens) >= 2:
                min_matches = _multitoken_min_required(len(content_tokens))
                lance_hits = [
                    lh
                    for lh in lance_hits
                    if sum(
                        1
                        for tok in content_tokens
                        if _token_matches_boundary(
                            tok,
                            f"{lh.act} {lh.ref} {lh.title or ''} {lh.snippet}".lower(),
                        )
                    )
                    >= min_matches
                ]

        # Promote LanceDB state/regulator hits whose act/alias + ref match the query or parsed citations
        exact_doc_ids = {h.doc_id for h in exact_hits}
        q_lower = q.query.lower()
        q_tokens = {t.lower() for t in _STATUTE_TOKEN_RE.findall(q.query)}
        act_aliases: dict[str, set[str]] = {}
        for alias_k, target_short in alias_map.items():
            act_aliases.setdefault(target_short.lower(), set()).add(alias_k.lower())
        exact_ref_pairs = {(a.lower(), r.lower()) for a, r in analyzed.exact_statute_refs}

        lance_promoted: list[StatuteHit] = []
        for lh in lance_hits:
            act_l = lh.act.lower()
            ref_l = lh.ref.lower()
            aliases_for_act = act_aliases.get(act_l, set()) | {act_l}
            act_matched = (
                (q.act is not None and q.act.strip().lower() in aliases_for_act)
                or any(
                    al in q_tokens
                    or (len(al) >= 4 and re.search(rf"(?<![A-Za-z0-9]){re.escape(al)}(?![A-Za-z0-9])", q_lower))
                    for al in aliases_for_act
                )
            )
            ref_matched = (
                (act_l, ref_l) in exact_ref_pairs
                or bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(ref_l)}(?![A-Za-z0-9])", q_lower))
            )
            if (act_l, ref_l) in exact_ref_pairs or (act_matched and ref_matched):
                if lh.doc_id not in exact_doc_ids:
                    exact_hits.append(lh.model_copy(update={"bm25_score": max(lh.bm25_score, 100.0)}))
                    exact_doc_ids.add(lh.doc_id)
            elif act_matched:
                lance_promoted.append(lh)

        # Lookup practitioner-shorthand / pari-materia equivalent statutory provisions
        equiv_hits: list[StatuteHit] = []
        if not q.category or q.category == "central_act":
            canon_q_act = alias_map.get(q.act.strip().lower(), q.act.strip()) if q.act else None
            for s_act, s_ref in analyzed.equivalent_statute_refs:
                if canon_q_act and canon_q_act.lower() not in {
                    s_act.lower(),
                    alias_map.get(s_act.lower(), "").lower(),
                }:
                    continue
                rec = self.backend.get_provision(
                    s_act,
                    s_ref,
                    jurisdiction=q.jurisdiction,
                    include_links=False,
                    commercial_safe_only=cs_only,
                )
                if rec is not None and rec.doc_id not in exact_doc_ids:
                    if q.status != "all" and rec.validity.status != q.status:
                        continue
                    equiv_hits.append(
                        StatuteHit(
                            doc_id=rec.doc_id,
                            act=rec.act,
                            jurisdiction=rec.jurisdiction,
                            kind=rec.kind,
                            ref=rec.ref,
                            title=rec.title,
                            status=rec.validity.status,
                            replaced_by=rec.validity.replaced_by,
                            snippet=rec.text[:400],
                            bm25_score=50.0,
                            provenance=rec.provenance,
                        )
                    )

        # 4. Weighted RRF (k=60) + definition promotion + exact-citation top-3 floor
        sql_w = 1.2 if (q.category is None and q.jurisdiction is None) else 1.0
        lance_w = 0.75 if (q.category is None and q.jurisdiction is None) else 0.95
        fused_pairs = reciprocal_rank_fusion(
            [
                (1.5, exact_hits),
                (1.15, lance_promoted),
                (1.1, equiv_hits),
                (sql_w, sqlite_hits),
                (lance_w, lance_hits),
            ],
            key_fn=lambda h: h.doc_id,
            k=60,
        )
        fused = [
            item.model_copy(
                update={"bm25_score": round(max(item.bm25_score, rrf_score * 100.0), 4)}
            )
            for item, rrf_score in fused_pairs
        ]
        if q.promote_definitions:
            fused = apply_definition_promotion(fused)
        ordered = apply_exact_citation_floor(fused, exact_hits, key_fn=lambda h: h.doc_id, floor=3)

        total = len(ordered)
        page = ordered[offset : offset + limit]
        has_more = (offset + len(page)) < total
        next_cursor = encode_cursor(offset + len(page)) if has_more else None

        return StatuteSearchResponse(
            query=q.query,
            query_mode=q.query_mode,
            total=total,
            returned=len(page),
            next_cursor=next_cursor,
            has_more=has_more,
            results=page,
            degraded_sources=degraded,
        )

    def search_judgments(self, q: JudgmentSearchQuery) -> JudgmentSearchResponse:
        cs_only = self._effective_cs(q.commercial_safe_only)
        limit = max(1, min(int(q.limit), 25))
        offset = decode_cursor(q.cursor, q.offset)
        degraded: list[str] = []

        year_from = q.year_from or (q.date_from.year if q.date_from else None)
        year_to = q.year_to or (q.date_to.year if q.date_to else None)
        tier = q.court_tier or "all"

        if cs_only and tier == "district_court":
            raise CommercialLicenseBlocked(
                "District Court dataset (DDL 81M, CC-BY-NC-SA-4.0) is blocked when commercial_safe_only=True.",
                license_spdx="CC-BY-NC-SA-4.0",
            )

        # Breadth guard for unfiltered HC/District scans
        narrow_year = year_from is not None and year_to is not None and (year_to - year_from) <= 5
        has_narrow_filter = any(
            [
                q.query and q.query.strip(),
                q.court,
                q.act,
                q.section,
                q.judge,
                q.author_judge,
                q.party,
                narrow_year,
                tier == "supreme_court",
            ]
        )
        if not has_narrow_filter:
            raise QueryTooBroad(
                "Judgment search over High Court / District tiers requires a query or at least one narrowing filter "
                "(court, act, judge, party, or date range <= 5 years).",
                hint="Provide query='...' or court='DHC' or date_from/date_to within 5 years.",
            )

        alias_map = self.backend.get_alias_map()
        analyzed = analyze_query(
            q.query,
            act=q.act,
            section=q.section,
            include_equivalent_provisions=q.include_equivalent_provisions,
            alias_map=alias_map,
            backend=self.backend,
        )
        expanded_strs = [f"{a} s.{r}" for a, r in analyzed.equivalent_statute_refs]

        # 1. Exact judgment citation hits
        exact_cards: list[JudgmentCard] = []
        for j_ref in analyzed.exact_judgment_refs:
            metas = self.backend.resolve_judgment_id(j_ref, commercial_safe_only=cs_only)
            for m in metas:
                exact_cards.append(SqliteStore._meta_to_card(m, matched_via="direct"))

        # 2. SQLite judgment metadata FTS
        sqlite_cards: list[JudgmentCard] = []
        if isinstance(self.backend, SqliteStore) and tier in ("all", "supreme_court", "high_court", "tribunal"):
            sqlite_cards = self.backend.search_judgments_fts(
                q.query,
                court=q.court if isinstance(q.court, (str, list)) else None,
                tier=tier,
                judge=q.judge,
                author_judge=q.author_judge,
                party=q.party,
                min_bench_strength=q.min_bench_strength,
                year_from=year_from,
                year_to=year_to,
                disposal_nature=q.disposal_nature,
                case_type=q.case_type,
                limit=max(25, offset + limit),
                commercial_safe_only=cs_only,
            )

        # 3. LanceDB SC & HC full-text BM25 (plus pari-materia equivalent expansion queries)
        lance_direct: list[JudgmentCard] = []
        lance_equiv: list[JudgmentCard] = []
        if self.lance is not None and (q.query or analyzed.exact_statute_refs or analyzed.equivalent_statute_refs):
            base_q = (q.query or "").strip()
            if not base_q and analyzed.exact_statute_refs:
                base_q = " ".join(f"{a} {r}" for a, r in analyzed.exact_statute_refs)
            try:
                c_code: str | None = None
                if isinstance(q.court, str):
                    # Pick Dattam numeric code if known
                    variants = court_code_variants(q.court)
                    c_code = next((v for v in variants if "_" in v), None)

                if base_q and tier in ("all", "supreme_court"):
                    for r in self.lance.search_sc(
                        base_q,
                        year_from=year_from,
                        year_to=year_to,
                        author_judge=q.author_judge,
                        min_bench_strength=q.min_bench_strength,
                        commercial_safe_only=cs_only,
                        limit=30,
                    ):
                        lance_direct.append(_lance_row_to_judgment_card(r, tier="supreme_court", matched_via="direct"))
                if base_q and tier in ("all", "high_court"):
                    hc_limit = 150 if (tier == "high_court" and q.court is None) else 30
                    for r in self.lance.search_hc(
                        base_q,
                        court_code=c_code,
                        bench=q.bench,
                        year_from=year_from,
                        year_to=year_to,
                        min_bench_strength=q.min_bench_strength,
                        disposal_nature=q.disposal_nature,
                        commercial_safe_only=cs_only,
                        limit=hc_limit,
                    ):
                        lance_direct.append(_lance_row_to_judgment_card(r, tier="high_court", matched_via="direct"))

                if analyzed.equivalent_statute_refs:
                    eq_q = " ".join(f"{base_q} {a} {r}".strip() for a, r in analyzed.equivalent_statute_refs)
                    if tier in ("all", "supreme_court"):
                        for r in self.lance.search_sc(
                            eq_q,
                            year_from=year_from,
                            year_to=year_to,
                            commercial_safe_only=cs_only,
                            limit=10,
                        ):
                            lance_equiv.append(
                                _lance_row_to_judgment_card(r, tier="supreme_court", matched_via="pari_materia_equivalent")
                            )
                    if tier in ("all", "high_court"):
                        eq_hc_limit = 30 if (tier == "high_court" and q.court is None) else 10
                        for r in self.lance.search_hc(
                            eq_q,
                            court_code=c_code,
                            year_from=year_from,
                            year_to=year_to,
                            commercial_safe_only=cs_only,
                            limit=eq_hc_limit,
                        ):
                            lance_equiv.append(
                                _lance_row_to_judgment_card(r, tier="high_court", matched_via="pari_materia_equivalent")
                            )
            except StorageUnavailable:
                degraded.append("lance_fts")

        # Multi-token word-boundary precision guard for LanceDB direct judgment hits
        if lance_direct and q.query and not exact_cards:
            _, raw_j_tokens = _extract_fts_tokens(q.query)
            j_tokens = _distinct_lower(raw_j_tokens)
            if len(j_tokens) >= 4:
                min_j_matches = _multitoken_min_required(len(j_tokens))
                lance_direct = [
                    card
                    for card in lance_direct
                    if sum(
                        1
                        for tok in j_tokens
                        if _token_matches_boundary(tok, _card_search_haystack(card))
                    )
                    >= min_j_matches
                ]

        # Word-boundary precision guard for High Court OR-expanded equivalent hits
        if lance_equiv and tier == "high_court" and not exact_cards:
            _, raw_eq_q_tokens = _extract_fts_tokens(q.query or "")
            q_eq_tokens = _distinct_lower(raw_eq_q_tokens)
            min_q_eq = _multitoken_min_required(len(q_eq_tokens)) if q_eq_tokens else 0
            equiv_pairs: list[list[str]] = []
            for eq_act, eq_ref in analyzed.equivalent_statute_refs:
                _, pair_toks = _extract_fts_tokens(f"{eq_act} {eq_ref}")
                if pair_toks:
                    equiv_pairs.append(_distinct_lower(pair_toks))
            filtered_equiv: list[JudgmentCard] = []
            for card in lance_equiv:
                hay = _card_search_haystack(card)
                q_ok = bool(q_eq_tokens) and (
                    sum(1 for tok in q_eq_tokens if _token_matches_boundary(tok, hay))
                    >= min_q_eq
                )
                pair_ok = any(
                    all(_token_matches_boundary(tok, hay) for tok in pair)
                    for pair in equiv_pairs
                )
                if q_ok or pair_ok:
                    filtered_equiv.append(card)
            lance_equiv = filtered_equiv

        # Strict anticipatory bail precision filter so plain BAIL APPLN. never slips through
        if (
            q.query
            and re.search(r"\banticipatory\b", q.query, re.IGNORECASE)
            and re.search(r"\bbail\b", q.query, re.IGNORECASE)
        ):
            lance_direct = [c for c in lance_direct if _has_anticipatory_bail_signal(c)]
            lance_equiv = [c for c in lance_equiv if _has_anticipatory_bail_signal(c)]
            sqlite_cards = [c for c in sqlite_cards if _has_anticipatory_bail_signal(c)]

        # 4. DuckDB Parquet metadata search when targeting specific tiers or metadata filters
        duck_cards: list[JudgmentCard] = []
        if self.duckdb is not None and tier in ("high_court", "district_court", "tribunal"):
            try:
                d_rows, _ = self.duckdb.search_judgments(
                    tier=tier,
                    query=q.query,
                    court=q.court,
                    state=q.court if (tier == "district_court" and isinstance(q.court, str)) else None,
                    bench=q.bench,
                    judge=q.judge or q.author_judge,
                    party=q.party,
                    act=q.act,
                    disposal_nature=q.disposal_nature,
                    case_type=q.case_type,
                    year_from=year_from,
                    year_to=year_to,
                    limit=25,
                    commercial_safe_only=cs_only,
                )
                for dr in d_rows:
                    d_raw = str(dr.get("decision_date") or "")[:10]
                    try:
                        d_date = Date.fromisoformat(d_raw) if d_raw else None
                    except ValueError:
                        d_date = None
                    is_nc = tier == "district_court"
                    d_court_code = dr.get("court_code")
                    d_court_name = _resolve_court_display_name(
                        dr.get("court_name"),
                        str(d_court_code) if d_court_code is not None else None,
                        fallback=str(tier),
                    )
                    duck_cards.append(
                        JudgmentCard(
                            case_id=str(dr.get("case_id") or dr.get("cnr") or ""),
                            cnr=dr.get("cnr"),
                            neutral_citation=dr.get("neutral_citation"),
                            title=str(dr.get("title") or "Untitled"),
                            court=d_court_name,
                            court_code=d_court_code,
                            court_tier=tier,
                            bench=dr.get("bench"),
                            decision_date=d_date,
                            author_judge=dr.get("author_judge") or dr.get("judge"),
                            bench_strength=int(dr["bench_strength"]) if dr.get("bench_strength") is not None else None,
                            disposal_nature=dr.get("disposal_nature"),
                            case_type=dr.get("case_type"),
                            acts_cited=[str(dr["act"])] if dr.get("act") else [],
                            matched_via="metadata",
                            text_availability="metadata_only" if is_nc else "on_demand_s3_pdf",
                            provenance=SourceProvenance(
                                source_id="ddl-district" if is_nc else f"parquet-{tier}",
                                source_name="DDL Judicial Dataset" if is_nc else f"Parquet {tier}",
                                source_url="https://www.devdatalab.org/judicial-data" if is_nc else "s3://nyaya-bucket/parquet",
                                license_spdx="CC-BY-NC-SA-4.0" if is_nc else "CC-BY-4.0",
                                commercial_safe=not is_nc,
                                terms_status="non_commercial_only" if is_nc else "cleared",
                                as_of=d_date or Date(2026, 11, 22),
                            ),
                        )
                    )
            except (QueryTooBroad, StorageUnavailable) as exc:
                logger.warning("DuckDB search_judgments fallback triggered: %s", exc)
                if not (sqlite_cards or lance_direct or lance_equiv or exact_cards):
                    raise

        # 5. Enrich metadata across sources, deduplicate by canonical judgment identity,
        # weighted RRF (k=60), authority ranking, and exact-citation top-3 floor
        meta_sources = exact_cards + sqlite_cards
        known_meta_ids = {c.case_id for c in meta_sources}
        for c in (*lance_direct, *lance_equiv, *duck_cards):
            if c.case_id and c.case_id not in known_meta_ids and c.cited_by_count is None:
                known_meta_ids.add(c.case_id)
                try:
                    resolved = self.backend.resolve_judgment_id(c.case_id, commercial_safe_only=cs_only)
                    for m in resolved:
                        meta_sources.append(SqliteStore._meta_to_card(m, matched_via="metadata"))
                except Exception as exc:
                    logger.debug("resolve_judgment_id enrichment skipped for %s: %s", c.case_id, exc)
        exact_cards = _dedup_cards(exact_cards)
        sqlite_cards = _dedup_cards(sqlite_cards)
        lance_direct = _dedup_cards(_enrich_cards_from_meta(lance_direct, meta_sources))
        lance_equiv = _dedup_cards(_enrich_cards_from_meta(lance_equiv, meta_sources))
        duck_cards = _dedup_cards(_enrich_cards_from_meta(duck_cards, meta_sources))

        fused_pairs = reciprocal_rank_fusion(
            [
                (1.5, exact_cards),
                (1.1, lance_direct),
                (1.0, sqlite_cards),
                (0.95, lance_equiv),
                (0.85, duck_cards),
            ],
            key_fn=_canonical_judgment_key,
            k=60,
        )
        rrf_scores = {c.case_id: sc for c, sc in fused_pairs}
        fused_cards = _enrich_cards_from_meta([c for c, _ in fused_pairs], meta_sources)

        ranked = rank_judgment_cards(
            fused_cards,
            rrf_scores,
            binding_for_court=q.binding_for_court,
            sort_by=q.sort_by,
        )
        ordered = apply_exact_citation_floor(
            ranked, exact_cards, key_fn=_canonical_judgment_key, floor=3
        )
        if tier == "high_court" and q.court is None and q.sort_by == "relevance":
            if exact_cards:
                exact_keys = {_canonical_judgment_key(c) for c in exact_cards}
                pinned = [c for c in ordered if _canonical_judgment_key(c) in exact_keys]
                rest = [c for c in ordered if _canonical_judgment_key(c) not in exact_keys]
                ordered = pinned + _interleave_by_court(rest)
            else:
                ordered = _interleave_by_court(ordered)

        total = len(ordered)
        page = ordered[offset : offset + limit]
        has_more = (offset + len(page)) < total
        next_cursor = encode_cursor(offset + len(page)) if has_more else None

        return JudgmentSearchResponse(
            query=q.query,
            total=total,
            total_is_estimate=False,
            returned=len(page),
            next_cursor=next_cursor,
            has_more=has_more,
            expanded_provisions=expanded_strs,
            results=page,
            coverage_note=(
                "Indexed BM25 covers Supreme Court and hot/reportable High Court judgments; "
                "broader High Court (17.77M), Tribunal (15 forums), and DDL District (2010-2018) "
                "records are searched via compacted Parquet metadata."
            ),
            degraded_sources=degraded,
        )

    def health(self) -> dict[str, Any]:
        return {
            "mode": "lexical_bm25",
            "lance": self.lance.health() if self.lance else {"status": "unconfigured"},
            "duckdb": "ok" if self.duckdb else "unconfigured",
        }


def build_retrieval_engine(
    settings: Settings,
    backend: CorpusBackend,
    *,
    lance: LanceStore | None = None,
    duckdb: DuckDBStore | None = None,
    embedder: Any = None,
    reranker: Any = None,
) -> LexicalRetrievalEngine:
    """Factory constructing the Phase-1 pure-lexical RetrievalEngine."""
    return LexicalRetrievalEngine(
        settings=settings,
        backend=backend,
        lance=lance,
        duckdb=duckdb,
        embedder=embedder,
        reranker=reranker,
    )
