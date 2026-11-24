"""Phase-1 pure lexical, metadata, and citation-graph RetrievalEngine."""

from __future__ import annotations

import base64
import json
import re
from datetime import date as Date
from typing import Any

from ..citations import court_code_variants
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
)
from ..storage.duckdb_store import DuckDBStore
from ..storage.lance_store import LanceStore
from ..storage.memory_backend import InMemoryCorpusBackend
from ..storage.protocols import CorpusBackend
from ..storage.sqlite_store import SqliteStore
from .query_analyzer import analyze_query
from .ranking import (
    apply_definition_promotion,
    apply_exact_citation_floor,
    rank_judgment_cards,
    reciprocal_rank_fusion,
)

_STATUTE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")


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
        else str(row.get("court_name") or court_code or "High Court")
    )
    return JudgmentCard(
        case_id=str(row["case_id"]),
        cnr=row.get("cnr") or None,
        neutral_citation=row.get("neutral_citation") or None,
        reporter_citations=reps,
        title=str(row.get("title") or "Untitled"),
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
        snippet=str(row.get("text") or "")[:450],
        text_availability="indexed_bm25",
        provenance=SourceProvenance(
            source_id=str(row.get("source_id") or ("dattam-sc" if tier == "supreme_court" else "dattam-hc")),
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
                status=q.status,
                exact_phrase=(q.query_mode == "exact_phrase"),
                limit=max(25, offset + limit),
                offset=0,
                commercial_safe_only=cs_only,
            )
        elif isinstance(self.backend, InMemoryCorpusBackend):
            sqlite_hits = self.backend.search_statutes_simple(
                q.query,
                act=q.act,
                commercial_safe_only=cs_only,
                limit=max(25, offset + limit),
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

        # 4. Weighted RRF (k=60) + definition promotion + exact-citation top-3 floor
        fused_pairs = reciprocal_rank_fusion(
            [
                (1.5, exact_hits),
                (1.15, lance_promoted),
                (1.0, sqlite_hits),
                (0.95, lance_hits),
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
                if base_q and tier in ("all", "supreme_court"):
                    for r in self.lance.search_sc(
                        base_q,
                        year_from=year_from,
                        year_to=year_to,
                        author_judge=q.author_judge,
                        min_bench_strength=q.min_bench_strength,
                        commercial_safe_only=cs_only,
                        limit=20,
                    ):
                        lance_direct.append(_lance_row_to_judgment_card(r, tier="supreme_court", matched_via="direct"))
                if base_q and tier in ("all", "high_court"):
                    c_code: str | None = None
                    if isinstance(q.court, str):
                        # Pick Dattam numeric code if known
                        variants = court_code_variants(q.court)
                        c_code = next((v for v in variants if "_" in v), None)
                    for r in self.lance.search_hc(
                        base_q,
                        court_code=c_code,
                        bench=q.bench,
                        year_from=year_from,
                        year_to=year_to,
                        min_bench_strength=q.min_bench_strength,
                        disposal_nature=q.disposal_nature,
                        commercial_safe_only=cs_only,
                        limit=20,
                    ):
                        lance_direct.append(_lance_row_to_judgment_card(r, tier="high_court", matched_via="direct"))

                if analyzed.equivalent_statute_refs:
                    eq_q = " ".join(f"{base_q} {a} {r}".strip() for a, r in analyzed.equivalent_statute_refs)
                    if tier in ("all", "supreme_court"):
                        for r in self.lance.search_sc(eq_q, commercial_safe_only=cs_only, limit=10):
                            lance_equiv.append(
                                _lance_row_to_judgment_card(r, tier="supreme_court", matched_via="pari_materia_equivalent")
                            )
                    if tier in ("all", "high_court"):
                        for r in self.lance.search_hc(eq_q, commercial_safe_only=cs_only, limit=10):
                            lance_equiv.append(
                                _lance_row_to_judgment_card(r, tier="high_court", matched_via="pari_materia_equivalent")
                            )
            except StorageUnavailable:
                degraded.append("lance_fts")

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
                    duck_cards.append(
                        JudgmentCard(
                            case_id=str(dr.get("case_id") or dr.get("cnr") or ""),
                            cnr=dr.get("cnr"),
                            neutral_citation=dr.get("neutral_citation"),
                            title=str(dr.get("title") or "Untitled"),
                            court=str(dr.get("court_name") or dr.get("court_code") or tier),
                            court_code=dr.get("court_code"),
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
            except (QueryTooBroad, StorageUnavailable):
                if not (sqlite_cards or lance_direct or lance_equiv or exact_cards):
                    raise

        # 5. Weighted RRF (k=60), authority ranking, and exact-citation top-3 floor
        fused_pairs = reciprocal_rank_fusion(
            [
                (1.5, exact_cards),
                (1.1, lance_direct),
                (1.0, sqlite_cards),
                (0.95, lance_equiv),
                (0.85, duck_cards),
            ],
            key_fn=lambda c: c.case_id,
            k=60,
        )
        rrf_scores = {c.case_id: sc for c, sc in fused_pairs}
        fused_cards = [c for c, _ in fused_pairs]

        ranked = rank_judgment_cards(
            fused_cards,
            rrf_scores,
            binding_for_court=q.binding_for_court,
            sort_by=q.sort_by,
        )
        ordered = apply_exact_citation_floor(ranked, exact_cards, key_fn=lambda c: c.case_id, floor=3)

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
