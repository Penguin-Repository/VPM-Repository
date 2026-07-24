"""Release archive download, integrity, and manifest validation."""

from __future__ import annotations

import hashlib
import re
import urllib.request
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from vpm_common import (
    EXPECTED_LICENSE,
    MAX_ARCHIVE_BYTES,
    MAX_PACKAGE_JSON_BYTES,
    UpdateError,
    strict_json_loads,
)

REQUIRED_TEXT_FIELDS = ("displayName", "description", "unity")


def download_archive(url: str, destination: Path) -> str:
    """Download an HTTPS release archive with a compressed-size limit."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "PenguinDOOM-VPM-Repository-Actions",
        },
    )
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        final_url = response.geturl()
        if not final_url.startswith("https://"):
            raise UpdateError(f"Release asset redirected to a non-HTTPS URL: {final_url!r}.")
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise UpdateError(
                    f"Release archive exceeds the {MAX_ARCHIVE_BYTES // (1024 * 1024)} MiB limit."
                )
            digest.update(chunk)
            output.write(chunk)
    if total == 0:
        raise UpdateError("Downloaded release archive is empty.")
    return digest.hexdigest()


def verify_archive_sha256(actual: str, expected: str) -> None:
    """Reject a downloaded archive whose digest differs from the dispatch."""
    if actual != expected:
        raise UpdateError(
            "Downloaded archive SHA-256 does not match the dispatch payload: "
            f"expected {expected}, received {actual}."
        )


def is_unsafe_zip_path(name: str) -> bool:
    """Return True for absolute, drive-qualified, empty, or traversing paths."""
    path = PurePosixPath(name.replace("\\", "/"))
    return (
        not name
        or name.startswith(("/", "\\"))
        or any(part in ("", ".", "..") for part in path.parts)
        or re.match(r"^[A-Za-z]:", name) is not None
    )


def read_limited(stream: BinaryIO, limit: int) -> bytes:
    """Read at most limit bytes and fail before retaining oversized content."""
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise UpdateError("package.json exceeds the 1 MiB safety limit after decompression.")
    return data


def validate_required_text_fields(manifest: Mapping[str, Any]) -> None:
    """Require VPM-facing text metadata to be present and non-empty."""
    for field in REQUIRED_TEXT_FIELDS:
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            raise UpdateError(f"package.json field {field!r} must be a non-empty string.")


def validate_optional_url(manifest: dict[str, Any], field: str, expected: str) -> None:
    """Allow an empty URL field or the exact trusted generated value."""
    value = manifest.get(field)
    if value not in (None, "", expected):
        raise UpdateError(f"package.json contains an unexpected {field}: {value!r}.")
    manifest[field] = expected


def find_package_info(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    """Audit archive paths and return the unique root package.json entry."""
    names: set[str] = set()
    package_info: zipfile.ZipInfo | None = None
    for info in archive.infolist():
        normalized_name = info.filename.replace("\\", "/")
        if is_unsafe_zip_path(normalized_name):
            raise UpdateError(f"ZIP contains an unsafe path: {info.filename!r}.")
        if normalized_name in names:
            raise UpdateError(f"ZIP contains a duplicate path: {normalized_name!r}.")
        names.add(normalized_name)
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise UpdateError(f"ZIP contains a symbolic link: {normalized_name!r}.")
        if normalized_name == "package.json":
            package_info = info

    if package_info is None:
        raise UpdateError("ZIP must contain package.json at its root.")
    if package_info.file_size > MAX_PACKAGE_JSON_BYTES:
        raise UpdateError("package.json exceeds the 1 MiB safety limit.")
    return package_info


def load_manifest(
    archive_path: Path,
    payload: Mapping[str, str],
    actual_sha256: str,
) -> dict[str, Any]:
    """Load, validate, and enrich the root package.json from a release ZIP."""
    if not zipfile.is_zipfile(archive_path):
        raise UpdateError("Downloaded release asset is not a valid ZIP archive.")

    with zipfile.ZipFile(archive_path) as archive:
        package_info = find_package_info(archive)
        with archive.open(package_info, "r") as package_stream:
            raw_manifest = read_limited(package_stream, MAX_PACKAGE_JSON_BYTES)

    try:
        manifest = strict_json_loads(raw_manifest.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        raise UpdateError(f"package.json is not valid strict UTF-8 JSON: {error}") from error

    if not isinstance(manifest, dict):
        raise UpdateError("package.json must contain a JSON object.")
    if manifest.get("name") != payload["package_name"]:
        raise UpdateError(
            f"package.json name {manifest.get('name')!r} does not match the dispatch payload."
        )
    if manifest.get("version") != payload["version"]:
        raise UpdateError(
            f"package.json version {manifest.get('version')!r} does not match the dispatch payload."
        )
    if manifest.get("license") != EXPECTED_LICENSE:
        raise UpdateError(
            f"package.json license must be {EXPECTED_LICENSE!r}, "
            f"received {manifest.get('license')!r}."
        )

    validate_required_text_fields(manifest)
    embedded_url = manifest.get("url")
    if embedded_url not in (None, "", payload["package_url"]):
        raise UpdateError(f"package.json contains an unexpected download URL: {embedded_url!r}.")

    validate_optional_url(manifest, "changelogUrl", payload["changelog_url"])
    validate_optional_url(manifest, "licensesUrl", payload["licenses_url"])
    manifest["url"] = payload["package_url"]
    manifest["zipSHA256"] = actual_sha256
    return manifest
