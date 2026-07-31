"""Immutable VPM listing updates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vpm_common import (
    PACKAGE_NAME,
    UpdateError,
    strict_json_dumps,
    strict_json_loads,
    version_key,
)


def load_listing(path: Path) -> dict[str, Any]:
    """Load a strict JSON VPM listing."""
    try:
        listing = strict_json_loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise UpdateError(f"{path} does not exist.") from error
    except ValueError as error:
        raise UpdateError(f"{path} is not valid strict JSON: {error}") from error

    if not isinstance(listing, dict) or not isinstance(listing.get("packages"), dict):
        raise UpdateError("vpm.json must contain an object with a packages object.")
    return listing


def immutable_release_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return metadata for immutable comparison without the yank projection."""
    comparable = dict(metadata)
    vrc_get = comparable.get("vrc-get")
    if isinstance(vrc_get, dict):
        comparable_vrc_get = dict(vrc_get)
        comparable_vrc_get.pop("yanked", None)
        if comparable_vrc_get:
            comparable["vrc-get"] = comparable_vrc_get
        else:
            del comparable["vrc-get"]
    return comparable


def update_listing(listing: dict[str, Any], manifest: dict[str, Any]) -> bool:
    """Add one immutable package version and keep deterministic ordering."""
    package_name = str(manifest["name"])
    version = str(manifest["version"])
    packages = listing["packages"]
    if not isinstance(packages, dict):
        raise UpdateError("vpm.json must contain a packages object.")
    has_existing_package_entry = package_name in packages
    existing_package_entry = packages.get(package_name)
    if has_existing_package_entry and not isinstance(existing_package_entry, dict):
        raise UpdateError(f"Package entry {package_name!r} is not an object.")
    package_entry = (
        {"versions": {}}
        if not has_existing_package_entry
        else dict(existing_package_entry)
    )
    versions = package_entry.get("versions", {})
    if not isinstance(versions, dict):
        raise UpdateError(f"Package entry {package_name!r} has no versions object.")

    if version in versions:
        existing = versions[version]
        if not isinstance(existing, dict):
            raise UpdateError(f"Version {version!r} metadata is not an object.")
        if immutable_release_metadata(existing) == immutable_release_metadata(manifest):
            return False
        raise UpdateError(
            f"Version {package_name}@{version} already exists with different metadata; "
            "published versions are immutable and will not be overwritten."
        )

    candidate_versions = dict(versions)
    candidate_versions[version] = manifest
    package_entry["versions"] = {
        key: candidate_versions[key]
        for key in sorted(candidate_versions, key=version_key, reverse=True)
    }
    candidate_packages = dict(packages)
    candidate_packages[package_name] = package_entry
    listing["packages"] = {
        key: candidate_packages[key] for key in sorted(candidate_packages)
    }
    return True


def apply_yank_policy(listing: dict[str, Any], policy: dict[str, Any]) -> bool:
    """Apply a validated policy by changing only each version's yank reason."""
    if policy.get("package") != PACKAGE_NAME:
        raise UpdateError(f"Yank policy package must be {PACKAGE_NAME!r}.")
    desired_yanks = policy.get("versions")
    if not isinstance(desired_yanks, dict):
        raise UpdateError("Yank policy versions must be an object.")

    packages = listing.get("packages")
    if not isinstance(packages, dict):
        raise UpdateError("vpm.json must contain a packages object.")
    package_entry = packages.get(PACKAGE_NAME)
    if package_entry is None:
        if desired_yanks:
            raise UpdateError("Yank policy references versions that are not listed.")
        return False
    if not isinstance(package_entry, dict):
        raise UpdateError(f"Package entry {PACKAGE_NAME!r} is not an object.")
    versions = package_entry.get("versions")
    if not isinstance(versions, dict):
        raise UpdateError(f"Package entry {PACKAGE_NAME!r} has no versions object.")

    for version in desired_yanks:
        if version not in versions:
            raise UpdateError(f"Yank policy references unknown version {version!r}.")

    changes: list[tuple[dict[str, Any], str | None]] = []
    for version, metadata in versions.items():
        if not isinstance(metadata, dict):
            raise UpdateError(f"Version {version!r} metadata is not an object.")
        if "vrc-get" in metadata and not isinstance(metadata["vrc-get"], dict):
            raise UpdateError(f"Version {version!r} vrc-get metadata is not an object.")

        desired_reason = desired_yanks.get(version)
        vrc_get = metadata.get("vrc-get")
        current_reason = None if vrc_get is None else vrc_get.get("yanked")
        if desired_reason is not None:
            if current_reason != desired_reason:
                changes.append((metadata, desired_reason))
        elif vrc_get is not None and "yanked" in vrc_get:
            changes.append((metadata, None))

    for metadata, desired_reason in changes:
        if desired_reason is None:
            vrc_get = metadata["vrc-get"]
            del vrc_get["yanked"]
            if not vrc_get:
                del metadata["vrc-get"]
        else:
            vrc_get = metadata.setdefault("vrc-get", {})
            vrc_get["yanked"] = desired_reason
    return bool(changes)


def write_listing(path: Path, listing: dict[str, Any]) -> None:
    """Atomically write a standards-compliant VPM listing."""
    try:
        serialized = strict_json_dumps(listing)
    except (TypeError, ValueError) as error:
        raise UpdateError(f"Refusing to write non-standard JSON to {path}: {error}") from error

    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(serialized, encoding="utf-8", newline="\n")
    temporary_path.replace(path)
