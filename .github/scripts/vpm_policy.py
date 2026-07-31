"""Load and verify the fixed Pure Base VPM yank policy."""

from __future__ import annotations

import base64
import urllib.parse
from collections.abc import Mapping
from typing import Any

from vpm_common import (
    COMMIT_RE,
    PACKAGE_NAME,
    SOURCE_REPOSITORY,
    UpdateError,
    parse_semver,
    strict_json_loads,
)
from vpm_payload import GITHUB_API_ROOT, ApiGetter, github_api_get

YANK_POLICY_PATH = "vpm-yanks.json"
YANK_POLICY_BRANCH = "master"
MAX_YANK_POLICY_BYTES = 64 * 1024


def load_yank_policy(raw: bytes) -> dict[str, Any]:
    """Parse one bounded, strict UTF-8 Pure Base yank policy document."""
    if len(raw) > MAX_YANK_POLICY_BYTES:
        raise UpdateError("Yank policy exceeds the 64 KiB safety limit.")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise UpdateError("Yank policy must not contain a UTF-8 BOM.")
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise UpdateError(f"Yank policy is not valid strict UTF-8 JSON: {error}") from error
    if text != text.rstrip(" \t\r\n"):
        raise UpdateError("Yank policy must not contain trailing bytes after its JSON document.")
    try:
        policy = strict_json_loads(text)
    except ValueError as error:
        raise UpdateError(f"Yank policy is not valid strict UTF-8 JSON: {error}") from error
    if not isinstance(policy, dict) or set(policy) != {
        "schemaVersion",
        "package",
        "versions",
    }:
        raise UpdateError("Yank policy must contain exactly schemaVersion, package, and versions.")
    if policy["schemaVersion"] != 1 or isinstance(policy["schemaVersion"], bool):
        raise UpdateError("Yank policy schemaVersion must be integer 1.")
    if policy["package"] != PACKAGE_NAME:
        raise UpdateError(f"Yank policy package must be {PACKAGE_NAME!r}.")
    versions = policy["versions"]
    if not isinstance(versions, dict):
        raise UpdateError("Yank policy versions must be an object.")
    for version, reason in versions.items():
        parse_semver(version)
        if not isinstance(reason, str) or not reason.strip():
            raise UpdateError(f"Yank reason for {version!r} must be a non-empty string.")
    return policy


def decode_policy_content(response: Mapping[str, Any], ref: str) -> bytes:
    """Decode the fixed-path GitHub contents response for one immutable ref."""
    content = response.get("content")
    if response.get("encoding") != "base64" or not isinstance(content, str):
        raise UpdateError(f"GitHub API returned invalid yank policy content for {ref}.")
    try:
        return base64.b64decode(content.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise UpdateError(f"GitHub API returned invalid base64 yank policy content for {ref}.") from error


def fetch_yank_policy_snapshot(
    policy_commit_sha: str,
    api_get: ApiGetter = github_api_get,
) -> dict[str, Any]:
    """Return a reachable immutable policy only when it matches master exactly."""
    if not COMMIT_RE.fullmatch(policy_commit_sha):
        raise UpdateError("policyCommitSha must be a 40-character hexadecimal Git commit SHA.")
    policy_commit_sha = policy_commit_sha.lower()
    repository_url = f"{GITHUB_API_ROOT}/repos/{SOURCE_REPOSITORY}"
    comparison = api_get(
        f"{repository_url}/compare/{policy_commit_sha}...{YANK_POLICY_BRANCH}"
    )
    if comparison.get("status") not in ("identical", "ahead"):
        raise UpdateError("policyCommitSha is not reachable from the master branch.")

    encoded_path = urllib.parse.quote(YANK_POLICY_PATH, safe="/")
    requested_raw = decode_policy_content(
        api_get(f"{repository_url}/contents/{encoded_path}?ref={policy_commit_sha}"),
        policy_commit_sha,
    )
    current_raw = decode_policy_content(
        api_get(f"{repository_url}/contents/{encoded_path}?ref={YANK_POLICY_BRANCH}"),
        YANK_POLICY_BRANCH,
    )
    requested_policy = load_yank_policy(requested_raw)
    load_yank_policy(current_raw)
    if requested_raw != current_raw:
        raise UpdateError("Policy snapshot no longer matches current master policy.")
    return requested_policy