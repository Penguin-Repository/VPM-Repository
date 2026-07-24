"""Immutable VPM listing updates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vpm_common import UpdateError, strict_json_dumps, strict_json_loads, version_key


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


def update_listing(listing: dict[str, Any], manifest: dict[str, Any]) -> bool:
    """Add one immutable package version and keep deterministic ordering."""
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
        key: versions[key] for key in sorted(versions, key=version_key, reverse=True)
    }
    listing["packages"] = {key: packages[key] for key in sorted(packages)}
    return True


def write_listing(path: Path, listing: dict[str, Any]) -> None:
    """Atomically write a standards-compliant VPM listing."""
    try:
        serialized = strict_json_dumps(listing)
    except (TypeError, ValueError) as error:
        raise UpdateError(f"Refusing to write non-standard JSON to {path}: {error}") from error

    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(serialized, encoding="utf-8", newline="\n")
    temporary_path.replace(path)
