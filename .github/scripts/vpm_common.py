"""Shared constants and strict JSON helpers for the VPM receiver."""

from __future__ import annotations

import json
import os
import re
from functools import total_ordering
from pathlib import Path
from typing import Any, Mapping

PACKAGE_NAME = "jp.penguin.purebase"
SOURCE_REPOSITORY = "Penguin-Repository/Pure-Base"
LEGACY_SOURCE_REPOSITORY = "Penguin-Repository/Pure-Base"
TRUSTED_SOURCE_REPOSITORIES = frozenset((SOURCE_REPOSITORY, LEGACY_SOURCE_REPOSITORY))
EXPECTED_LICENSE = "Apache-2.0"
LICENSE_PATH = "LICENSE"
VPM_PATH = Path("vpm.json")
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_JSON_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class UpdateError(RuntimeError):
    """Raised when dispatch data or package content cannot be trusted."""


@total_ordering
class SemanticVersion:
    """An immutable SemVer 2 version without build metadata."""

    def __init__(
        self,
        major: int,
        minor: int,
        patch: int,
        prerelease: tuple[tuple[int, int | str], ...] | None,
    ) -> None:
        self._core = (major, minor, patch)
        self._prerelease = prerelease

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._core == other._core and self._prerelease == other._prerelease

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        if self._core != other._core:
            return self._core < other._core
        if self._prerelease is None:
            return False
        if other._prerelease is None:
            return True
        for self_identifier, other_identifier in zip(
            self._prerelease, other._prerelease
        ):
            if self_identifier == other_identifier:
                continue
            if self_identifier[0] != other_identifier[0]:
                return self_identifier[0] < other_identifier[0]
            return self_identifier[1] < other_identifier[1]
        return len(self._prerelease) < len(other._prerelease)


def parse_semver(version: str) -> SemanticVersion:
    """Parse a strict ASCII SemVer 2 core and prerelease value."""
    if not isinstance(version, str) or not version.isascii():
        raise UpdateError(f"Repository contains an unsupported version key: {version!r}.")

    core_text, separator, prerelease_text = version.partition("-")
    core_identifiers = core_text.split(".")
    if len(core_identifiers) != 3 or any(
        not identifier.isdecimal()
        or (len(identifier) > 1 and identifier.startswith("0"))
        for identifier in core_identifiers
    ):
        raise UpdateError(f"Repository contains an unsupported version key: {version!r}.")

    prerelease: list[tuple[int, int | str]] = []
    if separator:
        if not prerelease_text:
            raise UpdateError(f"Repository contains an unsupported version key: {version!r}.")
        for identifier in prerelease_text.split("."):
            if not identifier or not all(character.isalnum() or character == "-" for character in identifier):
                raise UpdateError(f"Repository contains an unsupported version key: {version!r}.")
            if identifier.isdecimal():
                if len(identifier) > 1 and identifier.startswith("0"):
                    raise UpdateError(
                        f"Repository contains an unsupported version key: {version!r}."
                    )
                prerelease.append((0, int(identifier)))
            else:
                prerelease.append((1, identifier))

    return SemanticVersion(
        int(core_identifiers[0]),
        int(core_identifiers[1]),
        int(core_identifiers[2]),
        tuple(prerelease) if separator else None,
    )


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


def reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting duplicate keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key is not allowed: {key!r}")
        result[key] = value
    return result


def strict_json_loads(text: str) -> Any:
    """Parse standards-compliant JSON with unique keys and finite values."""
    return json.loads(
        text,
        object_pairs_hook=reject_duplicate_object_keys,
        parse_constant=reject_non_finite_constant,
    )


def strict_json_dumps(value: Any) -> str:
    """Serialize standards-compliant JSON and reject non-finite values."""
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def expected_urls(
    version: str,
    source_repository: str = SOURCE_REPOSITORY,
) -> tuple[str, str, str]:
    """Return the trusted asset name, asset URL, and release URL."""
    if source_repository not in TRUSTED_SOURCE_REPOSITORIES:
        raise UpdateError(f"Unsupported sourceRepository: {source_repository!r}.")
    asset_name = f"{PACKAGE_NAME}-{version}.zip"
    package_url = (
        f"https://github.com/{source_repository}/releases/download/"
        f"{version}/{asset_name}"
    )
    release_url = f"https://github.com/{source_repository}/releases/tag/{version}"
    return asset_name, package_url, release_url


def immutable_source_url(
    commit_sha: str,
    path: str,
    source_repository: str = SOURCE_REPOSITORY,
) -> str:
    """Build a source URL pinned to an immutable commit SHA."""
    if source_repository not in TRUSTED_SOURCE_REPOSITORIES:
        raise UpdateError(f"Unsupported sourceRepository: {source_repository!r}.")
    return f"https://github.com/{source_repository}/blob/{commit_sha}/{path}"


def version_key(version: str) -> SemanticVersion:
    """Return a sortable strict SemVer 2 value for a version string."""
    return parse_semver(version)
