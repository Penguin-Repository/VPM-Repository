"""Shared constants and strict JSON helpers for the VPM receiver."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

PACKAGE_NAME = "jp.penguin.purebase"
SOURCE_REPOSITORY = "PenguinDOOM/Pure-Base"
EXPECTED_LICENSE = "Apache-2.0"
LICENSE_PATH = "LICENSE"
VPM_PATH = Path("vpm.json")
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_JSON_BYTES = 1024 * 1024
STABLE_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class UpdateError(RuntimeError):
    """Raised when dispatch data or package content cannot be trusted."""


def required_value(values: Mapping[str, str], name: str) -> str:
    """Return a trimmed required value from a mapping."""
    value = values.get(name, "").strip()
    if not value:
        raise UpdateError(f"Required value {name} is empty.")
    return value


def required_env(name: str) -> str:
    """Return a trimmed required environment variable."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise UpdateError(f"Required environment variable {name} is empty.")
    return value


def reject_non_finite_constant(value: str) -> None:
    """Reject Python's non-standard NaN and Infinity JSON extensions."""
    raise ValueError(f"Non-finite JSON constant is not allowed: {value}")


def strict_json_loads(text: str) -> Any:
    """Parse standards-compliant JSON and reject non-finite constants."""
    return json.loads(text, parse_constant=reject_non_finite_constant)


def strict_json_dumps(value: Any) -> str:
    """Serialize standards-compliant JSON and reject non-finite values."""
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def expected_urls(version: str) -> tuple[str, str, str]:
    """Return the trusted asset name, asset URL, and release URL."""
    asset_name = f"{PACKAGE_NAME}-{version}.zip"
    package_url = (
        f"https://github.com/{SOURCE_REPOSITORY}/releases/download/"
        f"{version}/{asset_name}"
    )
    release_url = f"https://github.com/{SOURCE_REPOSITORY}/releases/tag/{version}"
    return asset_name, package_url, release_url


def immutable_source_url(commit_sha: str, path: str) -> str:
    """Build a source URL pinned to an immutable commit SHA."""
    return f"https://github.com/{SOURCE_REPOSITORY}/blob/{commit_sha}/{path}"


def version_key(version: str) -> tuple[int, int, int]:
    """Convert a stable semantic version into a sortable tuple."""
    match = STABLE_VERSION_RE.fullmatch(version)
    if not match:
        raise UpdateError(f"Repository contains an unsupported version key: {version!r}.")
    return tuple(int(part) for part in match.groups())
