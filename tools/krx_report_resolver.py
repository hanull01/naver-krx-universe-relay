"""Resolve one authorized private KRX generation for report workflows.

The resolver uses GitHub's Contents/Git Blob API with an injected token, reads
only ``data/krx_sync`` allowlisted files, and materializes report inputs under
the runner temporary directory.  It never clones the private repository and
never writes KRX payloads to the public repository.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from tools.krx_private_repo_source import (
    PrivateSourceError,
    load_verified_private_generation,
)
from tools.krx_sync_receipt import stage_m10_report_inputs


DEFAULT_REPOSITORY = "hanull01/KIS_Trading"
DEFAULT_REF = "codex/krx-private-sync"
DATE_RE = re.compile(r"^\d{8}$")


class ResolverError(RuntimeError):
    """The private KRX generation cannot be approved for report use."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _date(value: Any, field: str) -> str:
    text = str(value or "").replace("-", "")
    if not DATE_RE.fullmatch(text):
        raise ResolverError(f"INVALID_{field.upper()}")
    return text


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class GitHubContentsFetcher:
    """Authenticated read-only fetcher for the five private generation files."""

    def __init__(self, token: str, repository: str):
        if not token:
            raise ResolverError("SECRET_MISSING:KRX_PRIVATE_REPO_TOKEN")
        self.token = token
        self.repository = repository

    def _get(self, path: str) -> dict[str, Any]:
        raw_path, separator, query = path.partition("?")
        encoded = urllib.parse.quote(raw_path, safe="/:")
        if separator:
            encoded += "?" + query
        url = f"https://api.github.com/repos/{self.repository}/{encoded}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "krx-report-resolver",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=25, context=_ssl_context()) as response:
                payload = json.loads(response.read(20_000_000).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ResolverError(f"PRIVATE_REPO_HTTP_{exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError) as exc:
            raise ResolverError(f"PRIVATE_REPO_READ_FAILED:{type(exc).__name__}") from None
        if not isinstance(payload, dict):
            raise ResolverError("PRIVATE_REPO_RESPONSE_INVALID")
        return payload

    def fetch(self, repository: str, ref: str, path: str) -> bytes:
        if repository != self.repository:
            raise ResolverError("REPOSITORY_MISMATCH")
        metadata = self._get(f"contents/{path}?ref={urllib.parse.quote(ref, safe='')}")
        blob_sha = metadata.get("sha")
        if not isinstance(blob_sha, str):
            raise ResolverError(f"PRIVATE_FILE_SHA_MISSING:{path}")
        content = metadata.get("content")
        if isinstance(content, str) and content.strip():
            try:
                return base64.b64decode("".join(content.split()))
            except ValueError as exc:
                raise ResolverError(f"PRIVATE_FILE_BASE64_INVALID:{path}") from exc
        blob = self._get(f"git/blobs/{blob_sha}")
        encoded = blob.get("content")
        if blob.get("encoding") != "base64" or not isinstance(encoded, str):
            raise ResolverError(f"PRIVATE_BLOB_RESPONSE_INVALID:{path}")
        try:
            return base64.b64decode("".join(encoded.split()))
        except ValueError as exc:
            raise ResolverError(f"PRIVATE_BLOB_BASE64_INVALID:{path}") from exc


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_outputs(summary: Mapping[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    keys = {
        "status": "status",
        "sourceGeneration": "krx_generation_id",
        "manifestSha256": "krx_manifest_sha",
        "monitoringSha256": "krx_monitoring_sha",
        "targetDate": "krx_target_date",
        "entityCount": "krx_entity_count",
        "instrumentCount": "krx_instrument_count",
        "receiptStatus": "krx_receipt_status",
        "reportInputDir": "krx_report_input_dir",
        "metadataPath": "krx_metadata_path",
    }
    with Path(output_path).open("a", encoding="utf-8") as handle:
        for source, target in keys.items():
            value = summary.get(source, "")
            handle.write(f"{target}<<KRX_OUTPUT_EOF\n{value}\nKRX_OUTPUT_EOF\n")


def resolve(
    *,
    repository: str = DEFAULT_REPOSITORY,
    ref: str = DEFAULT_REF,
    expected_date: str | None = None,
    require_expected_date: bool = False,
    output_dir: str | Path,
) -> dict[str, Any]:
    if require_expected_date and not expected_date:
        raise ResolverError("EXPECTED_DATE_REQUIRED")
    token = os.environ.get("KRX_PRIVATE_REPO_TOKEN", "").strip()
    fetcher = GitHubContentsFetcher(token, repository)
    try:
        verified = load_verified_private_generation(
            fetcher,
            repository=repository,
            ref=ref,
            root="data/krx_sync",
            expected_date=expected_date,
        )
    except (PrivateSourceError, ResolverError) as exc:
        raise ResolverError(str(exc)) from exc

    target = _date(verified["tradingDate"], "target_date")
    source_generation = verified["generationId"]
    monitoring = verified["monitoring"]
    entities = monitoring.get("entities") or []
    instrument_count = sum(row.get("category") in {"stock", "etf"} for row in entities)
    metadata = {
        "schemaVersion": "krx_common_generation_resolution_v1",
        "repository": repository,
        "ref": ref,
        "sourceGeneration": source_generation,
        "targetDate": target,
        "expectedTradingDate": expected_date,
        "manifestSha256": _sha256(verified["payloads"]["manifest"]),
        "monitoringSha256": _sha256(verified["payloads"]["monitoring"]),
        "entityCount": len(entities),
        "instrumentCount": instrument_count,
        "publicationAuthorized": verified["transfer"].get("publicationAuthorized") is True,
        "receiptStatus": "VERIFIED",
        "staleEntityCount": 0,
        "futureEntityCount": 0,
        "duplicateEntityCount": 0,
        "historicalSource": "KRX",
        "currentSnapshotSource": "NAVER_SEPARATE",
        "historicalFallbackAllowed": False,
    }

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / "krx-resolution.json"
    previous = None
    if metadata_path.exists():
        try:
            previous = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            previous = None

    if previous == metadata and (output / "technicals.json").is_file():
        summary = {**metadata, "status": "NO_CHANGES", "reportInputDir": str(output), "metadataPath": str(metadata_path)}
        _write_outputs(summary)
        return summary

    payloads = verified["payloads"]
    receipt = {
        "schemaVersion": 2,
        "source": "KRX_OPEN_API",
        "tradingDate": target,
        "generationId": source_generation,
        "generationStatus": "VERIFIED",
        "publicationAuthorized": metadata["publicationAuthorized"],
        "artifacts": {
            "monitoring": {
                "name": "monitoring_interchange.json",
                "bytes": len(payloads["monitoring"]),
                "sha256": metadata["monitoringSha256"],
            },
            "generationManifest": {
                "name": "generation_manifest.json",
                "bytes": len(payloads["manifest"]),
                "sha256": metadata["manifestSha256"],
            },
        },
    }
    _write_json(output / "receipt.json", receipt)
    (output / "monitoring_interchange.json").write_bytes(payloads["monitoring"])
    (output / "generation_manifest.json").write_bytes(payloads["manifest"])
    stage_m10_report_inputs(output, output, expected_date=target)
    _write_json(metadata_path, metadata)
    summary = {**metadata, "status": "VERIFIED", "reportInputDir": str(output), "metadataPath": str(metadata_path)}
    _write_outputs(summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve an approved private KRX generation for reports")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--expected-date")
    parser.add_argument("--require-expected-date", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = resolve(
            repository=args.repository,
            ref=args.ref,
            expected_date=args.expected_date,
            require_expected_date=args.require_expected_date,
            output_dir=args.output_dir,
        )
    except (ResolverError, OSError, ValueError) as exc:
        print(f"KRX_RESOLVER_BLOCKED:{exc}", file=os.sys.stderr)
        return 2
    print(json.dumps({key: summary.get(key) for key in (
        "status", "sourceGeneration", "manifestSha256", "monitoringSha256",
        "targetDate", "entityCount", "instrumentCount", "receiptStatus",
    )}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
