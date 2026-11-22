"""Anonymous ap-south-1 S3 fetcher for Dattam Labs SC & HC OpenJustice buckets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    def sync_bucket(
        self,
        bucket: str,
        work_dir: Path,
        *,
        prefixes: list[str] | None = None,
        dry_run: bool = False,
        fixture_xml: str | None = None,
        fixture_index_json: dict[str, Any] | None = None,
    ) -> S3SyncSummary:
        base_url = self.bucket_base_url(bucket)
        listed: list[S3ObjectInfo] = []

        if fixture_xml is not None:
            listed.extend(parse_s3_list_objects_xml(fixture_xml))
        else:
            for prefix in prefixes or ["metadata/parquet/", "data/tar/"]:
                list_url = f"{base_url}/?list-type=2&prefix={prefix}"
                xml_bytes = self.http_fetcher.fetch_bytes(list_url)
                listed.extend(parse_s3_list_objects_xml(xml_bytes.decode("utf-8")))

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
