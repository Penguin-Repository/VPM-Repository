"""Repository-dispatch payload validation and GitHub tag verification."""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from vpm_common import (
    COMMIT_RE,
    LICENSE_PATH,
    PACKAGE_NAME,
    SHA256_RE,
    TRUSTED_SOURCE_REPOSITORIES,
    UpdateError,
    expected_urls,
    immutable_source_url,
    parse_semver,
    required_value,
    strict_json_loads,
)

GITHUB_API_ROOT = "https://api.github.com"
MAX_TAG_DEREFERENCE_DEPTH = 4
ApiGetter = Callable[[str], dict[str, Any]]


def validate_payload(values: Mapping[str, str] | None = None) -> dict[str, str]:
    """Validate environment-style dispatch values and normalize trusted URLs."""
    source = os.environ if values is None else values
    package_name = required_value(source, "PACKAGE_NAME")
    source_repository = required_value(source, "SOURCE_REPOSITORY")
    version = required_value(source, "VERSION")
    tag = required_value(source, "TAG")
    commit_sha = required_value(source, "COMMIT_SHA")
    policy_commit_sha = required_value(source, "POLICY_COMMIT_SHA")
    package_url = required_value(source, "PACKAGE_URL")
    expected_sha256 = required_value(source, "EXPECTED_SHA256").lower()
    release_url = required_value(source, "RELEASE_URL")

    if package_name != PACKAGE_NAME:
        raise UpdateError(f"Unsupported packageName: {package_name!r}.")
    if source_repository not in TRUSTED_SOURCE_REPOSITORIES:
        raise UpdateError(f"Unsupported sourceRepository: {source_repository!r}.")
    parse_semver(version)
    if tag != version:
        raise UpdateError(f"tag {tag!r} does not match version {version!r}.")
    if not COMMIT_RE.fullmatch(commit_sha):
        raise UpdateError("commitSha must be a 40-character hexadecimal Git commit SHA.")
    if not COMMIT_RE.fullmatch(policy_commit_sha):
        raise UpdateError("policyCommitSha must be a 40-character hexadecimal Git commit SHA.")
    if not SHA256_RE.fullmatch(expected_sha256):
        raise UpdateError("sha256 must be a 64-character hexadecimal SHA-256 value.")

    asset_name, trusted_package_url, trusted_release_url = expected_urls(
        version, source_repository
    )
    if package_url != trusted_package_url:
        raise UpdateError(
            "packageUrl does not match the immutable Pure Base release asset URL: "
            f"{trusted_package_url}"
        )
    if release_url != trusted_release_url:
        raise UpdateError(
            "releaseUrl does not match the expected Pure Base release URL: "
            f"{trusted_release_url}"
        )

    normalized_commit_sha = commit_sha.lower()
    return {
        "package_name": PACKAGE_NAME,
        "source_repository": source_repository,
        "version": version,
        "commit_sha": normalized_commit_sha,
        "policy_commit_sha": policy_commit_sha.lower(),
        "asset_name": asset_name,
        "package_url": package_url,
        "expected_sha256": expected_sha256,
        "release_url": trusted_release_url,
        "changelog_url": trusted_release_url,
        "licenses_url": immutable_source_url(
            normalized_commit_sha, LICENSE_PATH, source_repository
        ),
    }


def github_api_get(url: str) -> dict[str, Any]:
    """Read one GitHub REST JSON object using the workflow token when available."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Penguin-Repository-VPM-Repository-Actions",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except Exception as error:
        raise UpdateError(f"GitHub API request failed for {url}: {error}") from error

    try:
        result = strict_json_loads(body)
    except (UnicodeDecodeError, ValueError) as error:
        raise UpdateError(f"GitHub API returned invalid JSON for {url}: {error}") from error
    if not isinstance(result, dict):
        raise UpdateError(f"GitHub API returned a non-object response for {url}.")
    return result


def resolve_tag_commit(
    repository: str,
    tag: str,
    api_get: ApiGetter = github_api_get,
) -> str:
    """Resolve a lightweight or annotated Git tag to its commit SHA."""
    encoded_tag = urllib.parse.quote(tag, safe="")
    reference = api_get(f"{GITHUB_API_ROOT}/repos/{repository}/git/ref/tags/{encoded_tag}")
    target = reference.get("object")

    for _ in range(MAX_TAG_DEREFERENCE_DEPTH):
        if not isinstance(target, dict):
            raise UpdateError(f"Tag {tag!r} has no valid Git object.")
        object_type = target.get("type")
        object_sha = target.get("sha")
        if not isinstance(object_sha, str) or not COMMIT_RE.fullmatch(object_sha):
            raise UpdateError(f"Tag {tag!r} contains an invalid Git object SHA.")
        if object_type == "commit":
            return object_sha.lower()
        if object_type != "tag":
            raise UpdateError(f"Tag {tag!r} resolves to unsupported object type {object_type!r}.")
        annotated_tag = api_get(
            f"{GITHUB_API_ROOT}/repos/{repository}/git/tags/{object_sha}"
        )
        target = annotated_tag.get("object")

    raise UpdateError(f"Tag {tag!r} exceeds the supported annotation depth.")


def verify_release_commit(
    payload: Mapping[str, str],
    api_get: ApiGetter = github_api_get,
) -> None:
    """Confirm the released tag resolves to the dispatched source commit."""
    actual_commit = resolve_tag_commit(
        payload["source_repository"], payload["version"], api_get=api_get
    )
    if actual_commit != payload["commit_sha"]:
        raise UpdateError(
            f"Release tag {payload['version']!r} resolves to {actual_commit}, "
            f"not dispatched commit {payload['commit_sha']}."
        )
