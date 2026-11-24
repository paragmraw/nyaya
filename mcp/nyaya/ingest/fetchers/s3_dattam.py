"""Anonymous ap-south-1 S3 fetcher for Dattam Labs SC & HC OpenJustice buckets."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..state import IngestState, S3DeltaPlan, S3ObjectInfo, parse_s3_list_objects_xml
from .http import SafeHttpFetcher

ALLOWED_DATTAM_BUCKETS = frozenset(
    {
        "indian-supreme-court-judgments",
        "indian-high-court-judgments",
    }
)


@dataclass
class S3SyncSummary:
    """Result summary of syncing an OpenJustice S3 bucket."""

    bucket: str
    delta: S3DeltaPlan
    downloaded_keys: list[str] = field(default_factory=list)
    dry_run: bool = False


def parse_s3_list_objects_page(xml_text: str) -> tuple[list[S3ObjectInfo], str | None]:
    """Parse one AWS S3 ``ListObjectsV2`` XML page and return ``(objects, next_continuation_token)``."""
    objs = parse_s3_list_objects_xml(xml_text)
    root = ET.fromstring(xml_text.strip())
    is_truncated = False
    next_token: str | None = None
    for elem in root:
        tag = elem.tag.split("}", 1)[-1]
        text = (elem.text or "").strip()
        if tag == "IsTruncated":
            is_truncated = text.lower() == "true"
        elif tag == "NextContinuationToken" and text:
            next_token = text
    return objs, (next_token if is_truncated and next_token else None)


def _expand_year_prefixes(
    prefixes: list[str],
    year_from: int | None,
    year_to: int | None,
) -> list[str]:
    if year_from is None and year_to is None:
        return prefixes
    y_min = year_from if year_from is not None else 1950
    y_max = year_to if year_to is not None else max(2026, datetime.now(UTC).year)
    if y_max < y_min:
        return []
    expanded: list[str] = []
    for p in prefixes:
        if "year=" in p:
            expanded.append(p)
        elif p.endswith("/"):
            for yr in range(y_min, y_max + 1):
                expanded.append(f"{p}year={yr}/")
        else:
            expanded.append(p)
    return expanded


def _filter_objects_by_year(
    objects: list[S3ObjectInfo],
    year_from: int | None,
    year_to: int | None,
) -> list[S3ObjectInfo]:
    if year_from is None and year_to is None:
        return objects
    out: list[S3ObjectInfo] = []
    for obj in objects:
        if obj.year is not None:
            if year_from is not None and obj.year < year_from:
                continue
            if year_to is not None and obj.year > year_to:
                continue
        out.append(obj)
    return out


class DattamS3Fetcher:
    """Polls anonymous AWS Open Data S3 ``ListObjectsV2`` across all year prefixes."""

    def __init__(
        self,
        *,
        state: IngestState,
        http_fetcher: SafeHttpFetcher | None = None,
        region: str = "ap-south-1",
    ) -> None:
        self.state = state
        self.region = region
        self.http_fetcher = http_fetcher or SafeHttpFetcher(
            allowed_domains=[
                f"indian-supreme-court-judgments.s3.{region}.amazonaws.com",
                f"indian-high-court-judgments.s3.{region}.amazonaws.com",
            ]
        )

    def bucket_base_url(self, bucket: str) -> str:
        if bucket not in ALLOWED_DATTAM_BUCKETS:
            raise ValueError(f"Bucket {bucket!r} is not an allowlisted Dattam OpenJustice bucket.")
        return f"https://{bucket}.s3.{self.region}.amazonaws.com"

    def _list_prefix_paginated(self, base_url: str, prefix: str) -> list[S3ObjectInfo]:
        collected: list[S3ObjectInfo] = []
        token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            list_url = f"{base_url}/?list-type=2&prefix={quote(prefix, safe='/')}"
            if token is not None:
                list_url += f"&continuation-token={quote(token, safe='')}"
            xml_bytes = self.http_fetcher.fetch_bytes(list_url)
            page_objs, next_token = parse_s3_list_objects_page(xml_bytes.decode("utf-8"))
            collected.extend(page_objs)
            if not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
            token = next_token
        return collected

    def sync_bucket(
        self,
        bucket: str,
        work_dir: Path,
        *,
        prefixes: list[str] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        dry_run: bool = False,
        fixture_xml: str | None = None,
        fixture_index_json: dict[str, Any] | None = None,
    ) -> S3SyncSummary:
        base_url = self.bucket_base_url(bucket)
        listed: list[S3ObjectInfo] = []

        if fixture_xml is not None:
            listed.extend(parse_s3_list_objects_xml(fixture_xml))
        else:
            raw_prefixes = list(prefixes or ["metadata/parquet/", "data/tar/"])
            target_prefixes = _expand_year_prefixes(raw_prefixes, year_from, year_to)
            for prefix in target_prefixes:
                listed.extend(self._list_prefix_paginated(base_url, prefix))

        listed = _filter_objects_by_year(listed, year_from, year_to)

        delta = self.state.plan_s3_delta(bucket, listed, index_json=fixture_index_json)
        summary = S3SyncSummary(bucket=bucket, delta=delta, dry_run=dry_run)
        if dry_run:
            return summary

        raw_root = work_dir / "raw" / bucket
        to_fetch = [*delta.added, *delta.modified]
        for obj in to_fetch:
            dest = raw_root / obj.key
            if fixture_xml is not None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(json.dumps({"key": obj.key, "etag": obj.etag}).encode("utf-8"))
            else:
                obj_url = f"{base_url}/{obj.key}"
                self.http_fetcher.download_to_file(obj_url, dest)
            summary.downloaded_keys.append(obj.key)

        self.state.commit_s3_objects(bucket, to_fetch)
        return summary
