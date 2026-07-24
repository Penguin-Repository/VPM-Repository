#!/usr/bin/env python3
"""Validate a Pure Base repository_dispatch payload and update vpm.json."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from vpm_archive import download_archive, load_manifest, verify_archive_sha256
from vpm_common import UpdateError, VPM_PATH
from vpm_listing import load_listing, update_listing, write_listing
from vpm_payload import validate_payload, verify_release_commit


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


def process_update() -> tuple[dict[str, str], bool, str]:
    """Run the trusted release verification and immutable listing update."""
    payload = validate_payload()
    verify_release_commit(payload)

    with tempfile.TemporaryDirectory(prefix="vpm-update-") as temporary_directory:
        archive_path = Path(temporary_directory) / payload["asset_name"]
        actual_sha256 = download_archive(payload["package_url"], archive_path)
        verify_archive_sha256(actual_sha256, payload["expected_sha256"])
        manifest = load_manifest(archive_path, payload, actual_sha256)

    listing = load_listing(VPM_PATH)
    changed = update_listing(listing, manifest)
    if changed:
        write_listing(VPM_PATH, listing)
    return payload, changed, actual_sha256


def main() -> int:
    """Run the receiver and publish workflow outputs."""
    payload, changed, actual_sha256 = process_update()
    write_output("changed", "true" if changed else "false")
    write_output("package_name", payload["package_name"])
    write_output("version", payload["version"])
    append_summary(
        [
            "## VPM repository update",
            f"- Package: `{payload['package_name']}`",
            f"- Version: `{payload['version']}`",
            f"- Source: `{payload['source_repository']}@{payload['commit_sha']}`",
            f"- Changelog: {payload['changelog_url']}",
            f"- License: {payload['licenses_url']}",
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
