"""Hermetic in-memory CorpusBackend for zero-disk/zero-network unit tests."""

from __future__ import annotations

from datetime import date as Date

from ..citations import canonicalize_court, parse_single_citation
from ..exceptions import CommercialLicenseBlocked, NotFound
from ..models import (
    ActInfo,
    AmendmentRecord,
    BrowseResult,
    CitationEdgeRecord,
    ConcordanceEntry,
    CorpusCounts,
    CorpusStatusResponse,
    CourtInfo,
    CrossRefDirection,
    JudgmentCard,
    JudgmentMeta,
    PrecedentTrace,
    ProvisionHistory,
    ProvisionIndexEntry,
    ProvisionRecord,
    ProvisionRef,
    SourceProvenance,
    StatuteHit,
    StatutoryValidity,
    TocEntry,
    TraceDirection,
)


def _norm(val: str) -> str:
    return val.strip().lower()


class InMemoryCorpusBackend:
    """In-memory implementation of ``CorpusBackend`` for fast hermetic tests."""

    def __init__(
        self,
        *,
        acts: list[ActInfo] | None = None,
        provisions: list[ProvisionRecord] | None = None,
        concordance: list[ConcordanceEntry] | None = None,
        amendments: list[AmendmentRecord] | None = None,
        judgments: list[JudgmentMeta] | None = None,
        edges: list[CitationEdgeRecord] | None = None,
        courts: list[CourtInfo] | None = None,
        aliases: dict[str, str] | None = None,
        default_commercial_safe_only: bool = False,
    ) -> None:
        self.acts: dict[str, ActInfo] = {a.act_id: a for a in (acts or [])}
        self.provisions: dict[str, ProvisionRecord] = {p.doc_id: p for p in (provisions or [])}
        self.concordance: list[ConcordanceEntry] = list(concordance or [])
        self.amendments: list[AmendmentRecord] = list(amendments or [])
        self.judgments: dict[str, JudgmentMeta] = {j.case_id: j for j in (judgments or [])}
        self.edges: list[CitationEdgeRecord] = list(edges or [])
        self.courts: list[CourtInfo] = list(courts or [])
        self.aliases: dict[str, str] = {k.lower(): v for k, v in (aliases or {}).items()}
        for a in self.acts.values():
            self.aliases.setdefault(a.short_name.lower(), a.short_name)
        self.default_commercial_safe_only = default_commercial_safe_only

    def _effective_cs(self, commercial_safe_only: bool | None) -> bool:
        return (
            self.default_commercial_safe_only
            if commercial_safe_only is None
            else bool(commercial_safe_only)
        )

    def _resolve_act_short(self, act: str) -> str:
        key = _norm(act)
        return self.aliases.get(key, act.strip())

    def get_provision(
        self,
        act: str,
        ref: str,
        *,
        jurisdiction: str | None = None,
        include_text: bool = True,
        include_links: bool = True,
        commercial_safe_only: bool | None = None,
    ) -> ProvisionRecord | None:
        canon_act = self._resolve_act_short(act)
        target_ref = ref.strip().lower()
        for p in self.provisions.values():
            if p.act.lower() != canon_act.lower():
                continue
            if jurisdiction and p.jurisdiction.lower() != jurisdiction.lower():
                continue
            if p.ref.lower() == target_ref or p.ref_norm.lower() == target_ref:
                if self._effective_cs(commercial_safe_only) and not p.provenance.commercial_safe:
                    raise CommercialLicenseBlocked(
                        f"Provision {act} {ref} blocked under commercial_safe_only.",
                        license_spdx=p.provenance.license_spdx,
                    )
                if not include_text:
                    return p.model_copy(update={"text": ""})
                return p
        return None

    def browse_statute(
        self,
        act: str | None = None,
        *,
        query: str | None = None,
        jurisdiction: str | None = None,
        category: str | None = None,
        status: str | None = None,
        chapter: str | int | None = None,
        part: str | None = None,
        ref_from: str | None = None,
        ref_to: str | None = None,
        kind: str | None = None,
        include_text: bool = False,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> BrowseResult:
        cs = self._effective_cs(commercial_safe_only)
        if act is None:
            matched = [
                a
                for a in self.acts.values()
                if (not cs or a.provenance.commercial_safe)
                and (not jurisdiction or a.jurisdiction.lower() == jurisdiction.lower())
                and (not category or a.category.lower() == category.lower())
                and (not status or status == "all" or a.status == status)
                and (not query or query.lower() in a.short_name.lower() or query.lower() in a.full_name.lower())
            ]
            sliced = matched[offset : offset + limit]
            return BrowseResult(mode="catalog", acts=sliced, total=len(matched), returned=len(sliced))

        canon_act = self._resolve_act_short(act)
        act_info = next((a for a in self.acts.values() if a.short_name.lower() == canon_act.lower()), None)
        if act_info is None:
            raise NotFound(f"Act {act!r} not found.", kind="act")
        provs = [
            p
            for p in self.provisions.values()
            if p.act.lower() == act_info.short_name.lower()
            and (not cs or p.provenance.commercial_safe)
            and (chapter is None or (p.chapter_num or "").lower() == str(chapter).lower())
            and (part is None or (p.part or "").lower() == part.lower())
            and (kind is None or p.kind == kind)
        ]
        if chapter is None and part is None and kind is None and ref_from is None and ref_to is None:
            toc = [TocEntry(chapter_num=p.chapter_num, chapter_title=p.chapter_title, part=p.part, kind=p.kind, provision_count=1) for p in provs]
            return BrowseResult(mode="toc", act_info=act_info, chapters_or_parts=toc, total=len(toc), returned=len(toc))
        sliced_p = provs[offset : offset + limit]
        entries = [
            ProvisionIndexEntry(
                doc_id=p.doc_id,
                act=p.act,
                jurisdiction=p.jurisdiction,
                kind=p.kind,
                ref=p.ref,
                title=p.title,
                status=p.validity.status,
                snippet=p.text[:300] if include_text else None,
                provenance=p.provenance,
            )
            for p in sliced_p
        ]
        return BrowseResult(mode="provisions", act_info=act_info, provisions=entries, total=len(provs), returned=len(entries))

    def compare_provisions(
        self,
        act: str,
        ref: str,
        *,
        commercial_safe_only: bool | None = None,
    ) -> list[ConcordanceEntry]:
        prov = self.get_provision(act, ref, commercial_safe_only=commercial_safe_only)
        if prov is None:
            return []
        return [c for c in self.concordance if c.old_doc_id == prov.doc_id or c.new_doc_id == prov.doc_id]

    def get_provision_history(
        self,
        act: str,
        ref: str | None = None,
        *,
        amendment_number: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> ProvisionHistory:
        canon_act = self._resolve_act_short(act)
        matched = [
            am
            for am in self.amendments
            if am.act.lower() == canon_act.lower()
            and (amendment_number is None or am.number == amendment_number)
            and (year_from is None or (am.year or 0) >= year_from)
            and (year_to is None or (am.year or 9999) <= year_to)
            and (ref is None or ref in am.provisions_affected)
        ]
        sliced = matched[offset : offset + limit]
        return ProvisionHistory(act=canon_act, ref=ref, amendments=sliced, total=len(matched))

    def get_cross_refs(
        self,
        doc_id: str,
        *,
        direction: CrossRefDirection = "both",
        relation: str | None = None,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> tuple[list[CitationEdgeRecord], int]:
        matched = [
            e
            for e in self.edges
            if (
                (direction == "from" and e.from_id == doc_id)
                or (direction == "to" and e.to_id == doc_id)
                or (direction == "both" and (e.from_id == doc_id or e.to_id == doc_id))
            )
            and (relation is None or e.relation == relation)
        ]
        return matched[offset : offset + limit], len(matched)

    def resolve_judgment_id(
        self,
        identifier: str,
        *,
        court: str | None = None,
        year: int | None = None,
        commercial_safe_only: bool | None = None,
    ) -> list[JudgmentMeta]:
        cs = self._effective_cs(commercial_safe_only)
        ident = identifier.strip().lower()
        exact: list[JudgmentMeta] = []
        for jm in self.judgments.values():
            cands = {
                jm.case_id.lower(),
                (jm.cnr or "").lower(),
                (jm.neutral_citation or "").lower(),
                (jm.air_citation or "").lower(),
                (jm.scc_citation or "").lower(),
                (jm.scr_citation or "").lower(),
            }
            if ident in cands:
                exact.append(jm)
        if not exact:
            parsed = parse_single_citation(identifier)
            if parsed is not None:
                p_ref = parsed.base_ref.lower()
                for jm in self.judgments.values():
                    cands = {
                        (jm.cnr or "").lower(),
                        (jm.neutral_citation or "").lower(),
                        (jm.air_citation or "").lower(),
                        (jm.scc_citation or "").lower(),
                        (jm.scr_citation or "").lower(),
                    }
                    if p_ref in cands:
                        exact.append(jm)
        if exact:
            for jm in exact:
                if cs and not jm.provenance.commercial_safe:
                    raise CommercialLicenseBlocked(
                        "Judgment blocked under commercial_safe_only.",
                        license_spdx=jm.provenance.license_spdx,
                    )
            return exact
        return [
            jm
            for jm in self.judgments.values()
            if ident in jm.title.lower()
            and (not cs or jm.provenance.commercial_safe)
            and (court is None or (canonicalize_court(court) or court).lower() == jm.court_code.lower())
            and (year is None or jm.year == year)
        ]

    def trace_precedent(
        self,
        case_id: str,
        *,
        direction: TraceDirection = "cited_by",
        court: str | None = None,
        min_bench_strength: int | None = None,
        larger_bench_only: bool = False,
        date_from: Date | None = None,
        limit: int = 25,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> PrecedentTrace:
        metas = self.resolve_judgment_id(case_id, commercial_safe_only=commercial_safe_only)
        if not metas:
            raise NotFound(f"Judgment {case_id!r} not found.", kind="judgment")
        target = metas[0]
        card = JudgmentCard(
            case_id=target.case_id,
            cnr=target.cnr,
            neutral_citation=target.neutral_citation,
            reporter_citations=target.reporter_citations,
            title=target.title,
            court=target.court_name,
            court_code=target.court_code,
            court_tier=target.court_tier,
            bench_strength=target.bench_strength,
            provenance=target.provenance,
        )
        return PrecedentTrace(
            target=card,
            precedent_status="unverified_automated",
            verification_notice="In-memory test citation graph (unverified_automated).",
            edges=[],
            total=0,
        )

    def search_statutes_simple(
        self,
        query: str,
        *,
        act: str | None = None,
        commercial_safe_only: bool | None = None,
        limit: int = 10,
    ) -> list[StatuteHit]:
        cs = self._effective_cs(commercial_safe_only)
        q_tokens = [t for t in query.lower().split() if t]
        canon_act = self._resolve_act_short(act) if act else None
        hits: list[StatuteHit] = []
        for p in self.provisions.values():
            if cs and not p.provenance.commercial_safe:
                continue
            if canon_act and p.act.lower() != canon_act.lower():
                continue
            hay = f"{p.act} {p.ref} {p.title or ''} {p.text}".lower()
            score = sum(1.0 for t in q_tokens if t in hay)
            if score > 0:
                hits.append(
                    StatuteHit(
                        doc_id=p.doc_id,
                        act=p.act,
                        jurisdiction=p.jurisdiction,
                        kind=p.kind,
                        ref=p.ref,
                        title=p.title,
                        status=p.validity.status,
                        replaced_by=p.validity.replaced_by,
                        snippet=p.text[:400],
                        bm25_score=score,
                        provenance=p.provenance,
                    )
                )
        hits.sort(key=lambda h: h.bm25_score, reverse=True)
        return hits[:limit]

    def list_courts(self, query: str | None = None) -> list[CourtInfo]:
        if not query:
            return list(self.courts)
        ql = query.lower()
        return [c for c in self.courts if ql in c.court_code.lower() or ql in c.court_name.lower()]

    def get_alias_map(self) -> dict[str, str]:
        return dict(self.aliases)

    def corpus_status(
        self,
        *,
        court: str | None = None,
        include_sources: bool = True,
        commercial_safe_only: bool | None = None,
    ) -> CorpusStatusResponse:
        return CorpusStatusResponse(
            tiers={"memory_backend": "ok"},
            counts=CorpusCounts(
                central_acts=len(self.acts),
                provisions=len(self.provisions),
                sc_judgments=len(self.judgments),
                citation_edges=len(self.edges),
            ),
        )

    def close(self) -> None:
        return None

    @classmethod
    def from_sample_data(cls) -> InMemoryCorpusBackend:
        prov = SourceProvenance(
            source_id="memory-sample",
            source_name="In-Memory Sample Corpus",
            source_url="https://example.invalid/nyaya",
            license_spdx="CC-BY-4.0",
            commercial_safe=True,
            terms_status="cleared",
            as_of=Date(2026, 11, 22),
        )
        act_ipc = ActInfo(
            act_id="ipc-1860",
            short_name="IPC",
            full_name="The Indian Penal Code, 1860",
            year=1860,
            status="repealed",
            replaced_by_act="bns-2023",
            provenance=prov,
        )
        act_bns = ActInfo(
            act_id="bns-2023",
            short_name="BNS",
            full_name="The Bharatiya Nyaya Sanhita, 2023",
            year=2023,
            status="in_force",
            provenance=prov,
        )
        p_ipc = ProvisionRecord(
            doc_id="ipc-1860:302",
            act_id="ipc-1860",
            act="IPC",
            act_full_name=act_ipc.full_name,
            ref="302",
            ref_norm="302",
            title="Punishment for murder",
            text="Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.",
            validity=StatutoryValidity(
                status="repealed",
                repeal_date=Date(2024, 7, 1),
                replaced_by=[ProvisionRef(act="BNS", ref="103", title="Punishment for murder")],
            ),
            provenance=prov,
        )
        p_bns = ProvisionRecord(
            doc_id="bns-2023:103",
            act_id="bns-2023",
            act="BNS",
            act_full_name=act_bns.full_name,
            ref="103",
            ref_norm="103",
            title="Punishment for murder",
            text="Whoever commits murder shall be punished with death or imprisonment for life, and shall also be liable to fine.",
            validity=StatutoryValidity(status="in_force", commencement_date=Date(2024, 7, 1)),
            provenance=prov,
        )
        jm = JudgmentMeta(
            case_id="sc:1973:kesavananda",
            court_tier="supreme_court",
            court_code="SCI",
            court_name="Supreme Court of India",
            air_citation="AIR 1973 SC 1461",
            scc_citation="(1973) 4 SCC 225",
            reporter_citations=["AIR 1973 SC 1461", "(1973) 4 SCC 225"],
            title="Kesavananda Bharati v. State of Kerala",
            decision_date=Date(1973, 4, 24),
            year=1973,
            bench_strength=13,
            provenance=prov,
        )
        return cls(acts=[act_ipc, act_bns], provisions=[p_ipc, p_bns], judgments=[jm])
