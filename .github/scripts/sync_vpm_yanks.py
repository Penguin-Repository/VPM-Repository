#!/usr/bin/env python3
"""Synchronize vpm.json yank markers from the fixed Pure Base policy."""

from __future__ import annotations

import os
import sys
from typing import Any

from vpm_common import PACKAGE_NAME, SOURCE_REPOSITORY, UpdateError, VPM_PATH, required_env
from vpm_listing import apply_yank_policy, load_listing, write_listing
from vpm_policy import fetch_yank_policy_snapshot


def write_output(name: str, value: str) -> None:
    """Write one GitHub Actions step output when available."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")


def append_summary(lines: list[str]) -> None:
    """Append a human-readable GitHub Actions summary."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")


def process_yank_sync() -> tuple[dict[str, Any], str, bool]:
    """Verify the fixed policy snapshot and apply its desired yank state."""
    if required_env("PACKAGE_NAME") != PACKAGE_NAME:
        raise UpdateError(f"Unsupported packageName: {required_env('PACKAGE_NAME')!r}.")
    if required_env("SOURCE_REPOSITORY") != SOURCE_REPOSITORY:
        raise UpdateError(
            f"Unsupported sourceRepository: {required_env('SOURCE_REPOSITORY')!r}."
        )
    policy_commit_sha = required_env("POLICY_COMMIT_SHA").lower()
    policy = fetch_yank_policy_snapshot(policy_commit_sha)
    listing = load_listing(VPM_PATH)
    changed = apply_yank_policy(listing, policy)
    if changed:
        write_listing(VPM_PATH, listing)
    return policy, policy_commit_sha, changed


def main() -> int:
    """Run the policy-only yank synchronization and publish its result."""
    policy, policy_commit_sha, changed = process_yank_sync()
    write_output("changed", "true" if changed else "false")
    write_output("package_name", policy["package"])
    write_output("policy_commit_sha", policy_commit_sha)
    write_output("yanked_versions", str(len(policy["versions"])))
    append_summary(
        [
            "## VPM yank synchronization",
            f"- Package: `{policy['package']}`",
            f"- Policy commit: `{policy_commit_sha}`",
            f"- Yanked versions: `{len(policy['versions'])}`",
            f"- Result: `{'updated' if changed else 'already current'}`",
        ]
    )
    print(
        f"{'Updated' if changed else 'No change for'} "
        f"{policy['package']} yank policy at {policy_commit_sha} "
        f"({len(policy['versions'])} versions)."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)