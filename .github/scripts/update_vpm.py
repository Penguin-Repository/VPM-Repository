#!/usr/bin/env python3
"""Validate a Pure Base repository_dispatch payload and update vpm.json."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

PACKAGE_NAME = "jp.penguin.purebase"
SOURCE_REPOSITORY = "PenguinDOOM/Pure-Base"
VPM_PATH = Path("vpm.json")
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_JSON_BYTES = 1024 * 1024
STABLE_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class UpdateError(RuntimeError):
    """Raised when the dispatch or package cannot be trusted."""


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise UpdateError(f"Required environment variable {name} is empty.")
    return value


def write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")


def append_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")


def expected_urls(version: str) -> tuple[str, str, str]:
    asset_name = f"{PACKAGE_NAME}-{version}.zip"
    package_url = (
        f"https://github.com/{SOURCE_REPOSITORY}/releases/download/"
        f"{version}/{asset_name}"
    )
    release_url = f"https://github.com/{SOURCE_REPOSITORY}/releases/tag/{version}"
    return asset_name, package_url, release_url


def validate_payload() -> dict[str, str]:
    package_name = required_env("PACKAGE_NAME")
    source_repository = required_env("SOURCE_REPOSITORY")
    version = required_env("VERSION")
    tag = required_env("TAG")
    commit_sha = required_env("COMMIT_SHA")
    package_url = required_env("PACKAGE_URL")
    expected_sha256 = required_env("EXPECTED_SHA256").lower()
    release_url = required_env("RELEASE_URL")

    if package_name != PACKAGE_NAME:
        raise UpdateError(f"Unsupported packageName: {package_name!r}.")
    if source_repository != SOURCE_REPOSITORY:
        raise UpdateError(f"Unsupported sourceRepository: {source_repository!r}.")
    if not STABLE_VERSION_RE.fullmatch(version):
        raise UpdateError(f"Only stable semantic versions are accepted: {version!r}.")
    if tag != version:
        raise UpdateError(f"tag {tag!r} does not match version {version!r}.")
    if not COMMIT_RE.fullmatch(commit_sha):
        raise UpdateError("commitSha must be a 40-character hexadecimal Git commit SHA.")
    if not SHA_RE.fullmatch(expected_sha256):
        raise UpdateError("sha256 must be a 64-character hexadecimal SHA-256 value.")

    asset_name, trusted_package_url, trusted_release_url = expected_urls(version)
    if package_url != trusted_package_url:
        raise UpdateError(
            "packageurl does not match the immutable Pure Base release asset URL: "
            f"{trusted_package_url}"
        )
    if release_url.rstrip("/") != trusted_release_url:
        raise UpdateError(
            "releaseUrl does not match the expected Pure Base release URL: "
            f"{trusted_release_url}"
        )

    return {
        "package_name": package_name,
        "source_repository": source_repository,
        "version": version,
        "commit_sha": commit_sha.lower(),
        "asset_name": asset_name,
        "package_url": package_url,
        "expected_sha256": expected_sha256,
        "release_url": trusted_release_url,
    }


def download_archive(url: str, destination: Path) -> str:
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


def is_unsafe_zip_path(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return (
        not name
        or name.startswith(("/", "\\"))
        or any(part in ("", ".", "..") for part in path.parts)
        or re.match(r"^[A-Za-z]:", name) is not None
    )


def load_manifest(archive_path: Path, payload: dict[str, str], actual_sha256: str) -> dict[str, Any]:
    if not zipfile.is_zipfile(archive_path):
        raise UpdateError("Downloaded release asset is not a valid ZIP archive.")

    with zipfile.ZipFile(archive_path) as archive:
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

        raw_manifest = archive.read(package_info)

    try:
        manifest = json.loads(raw_manifest.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError(f"package.json is not valid UTF-8 JSON: {error}") from error

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

    embedded_url = manifest.get("url")
    if embedded_url not in (None, "", payload["package_url"]):
        raise UpdateError(f"package.json contains an unexpected download URL: {embedded_url!r}.")

    manifest["url"] = payload["package_url"]
    manifest["zipSHA256"] = actual_sha256
    return manifest


def version_key(version: str) -> tuple[int, int, int]:
    match = STABLE_VERSION_RE.fullmatch(version)
    if not match:
        raise UpdateError(f"Repository contains an unsupported version key: {version!r}.")
    return tuple(int(part) for part in match.groups())


def load_listing() -> dict[str, Any]:
    try:
        listing = json.loads(VPM_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise UpdateError(f"{VPM_PATH} does not exist.") from error
    except json.JSONDecodeError as error:
        raise UpdateError(f"{VPM_PATH} is not valid JSON: {error}") from error

    if not isinstance(listing, dict) or not isinstance(listing.get("packages"), dict):
        raise UpdateError("vpm.json must contain an object with a packages object.")
    return listing


def update_listing(listing: dict[str, Any], manifest: dict[str, Any]) -> bool:
    package_name = str(manifest["name"])
    version = str(manifest["version"])
    packages = listing["packages"]
    package_entry = packages.setdefault(package_name, {"versions": {}})
    if not isinstance(package_entry, dict):
        raise UpdateError(f"Package entry {package_name!r} is not an object.")
    versions = package_entry.setdefault("versions", {})
    if not isinstance(versions, dict):
        raise UpdateError(f"Package entry {package_name!r} has no versions object.")

    existing = versions.get(version)
    if existing is not None:
        if existing == manifest:
            return False
        raise UpdateError(
            f"Version {package_name}@{version} already exists with different metadata; "
            "published versions are immutable and will not be overwritten."
        )

    versions[version] = manifest
    package_entry["versions"] = {
        key: versions[key]
        for key in sorted(versions, key=version_key, reverse=True)
    }
    listing["packages"] = {
        key: packages[key]
        for key in sorted(packages)
    }
    return True


def write_listing(listing: dict[str, Any]) -> None:
    serialized = json.dumps(listing, ensure_ascii=False, indent=2) + "\n"
    temporary_path = VPM_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(serialized, encoding="utf-8", newline="\n")
    temporary_path.replace(VPM_PATH)


def main() -> int:
    payload = validate_payload()
    with tempfile.TemporaryDirectory(prefix="vpm-update-") as temporary_directory:
        archive_path = Path(temporary_directory) / payload["asset_name"]
        actual_sha256 = download_archive(payload["package_url"], archive_path)
        if actual_sha256 != payload["expected_sha256"]:
            raise UpdateError(
                "Downloaded archive SHA-256 does not match the dispatch payload: "
                f"expected {payload['expected_sha256']}, received {actual_sha256}."
            )
        manifest = load_manifest(archive_path, payload, actual_sha256)

    listing = load_listing()
    changed = update_listing(listing, manifest)
    if changed:
        write_listing(listing)

    write_output("changed", "true" if changed else "false")
    write_output("package_name", payload["package_name"])
    write_output("version", payload["version"])
    append_summary(
        [
            "## VPM repository update",
            f"- Package: `{payload['package_name']}`",
            f"- Version: `{payload['version']}`",
            f"- Source: `{payload['source_repository']}@{payload['commit_sha']}`",
            f"- SHA-256: `{payload['expected_sha256']}`",
            f"- Result: `{'updated' if changed else 'already current'}`",
        ]
    )
    print(
        f"{'Updated' if changed else 'No change for'} "
        f"{payload['package_name']} {payload['version']} ({actual_sha256})."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
