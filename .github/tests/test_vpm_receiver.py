"""Unit tests for the Pure Base VPM dispatch receiver."""

from __future__ import annotations

import io
import json
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from vpm_archive import (  # noqa: E402
    is_unsafe_zip_path,
    load_manifest,
    read_limited,
    verify_archive_sha256,
)
from vpm_common import (  # noqa: E402
    MAX_PACKAGE_JSON_BYTES,
    UpdateError,
    strict_json_dumps,
    strict_json_loads,
)
from vpm_listing import load_listing, update_listing, write_listing  # noqa: E402
from vpm_payload import resolve_tag_commit, validate_payload, verify_release_commit  # noqa: E402

COMMIT_SHA = "a" * 40
OTHER_COMMIT_SHA = "b" * 40
DIGEST = "c" * 64
VERSION = "0.2.0"
PACKAGE_URL = (
    "https://github.com/PenguinDOOM/Pure-Base/releases/download/"
    f"{VERSION}/jp.penguin.purebase-{VERSION}.zip"
)
RELEASE_URL = f"https://github.com/PenguinDOOM/Pure-Base/releases/tag/{VERSION}"


def valid_payload() -> dict[str, str]:
    return {
        "package_name": "jp.penguin.purebase",
        "source_repository": "PenguinDOOM/Pure-Base",
        "version": VERSION,
        "commit_sha": COMMIT_SHA,
        "asset_name": f"jp.penguin.purebase-{VERSION}.zip",
        "package_url": PACKAGE_URL,
        "expected_sha256": DIGEST,
        "release_url": RELEASE_URL,
        "changelog_url": RELEASE_URL,
        "licenses_url": (
            "https://github.com/PenguinDOOM/Pure-Base/blob/"
            f"{COMMIT_SHA}/LICENSE"
        ),
    }


def valid_manifest() -> dict[str, object]:
    return {
        "name": "jp.penguin.purebase",
        "displayName": "PureBase",
        "version": VERSION,
        "author": {"name": "Penguin"},
        "unity": "2022.3",
        "description": "Minimal base shader for Shader-Core",
        "vpmDependencies": {"jp.lilxyzw.shadercore": "0.1.5"},
        "changelogUrl": "",
        "licensesUrl": "",
        "license": "Apache-2.0",
        "keywords": ["Shader"],
        "url": "",
        "legacyFolders": {"Assets\\PureBase": ""},
    }


def write_zip(path: Path, manifest_bytes: bytes, extra_name: str | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("package.json", manifest_bytes)
        if extra_name is not None:
            archive.writestr(extra_name, b"unsafe")


class StrictJsonTests(unittest.TestCase):
    def test_rejects_non_finite_constants_on_load(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                strict_json_loads(f'{{"value": {token}}}')

    def test_rejects_non_finite_values_on_write(self) -> None:
        with self.assertRaises(ValueError):
            strict_json_dumps({"value": math.nan})


class PayloadTests(unittest.TestCase):
    def test_accepts_canonical_payload(self) -> None:
        values = {
            "PACKAGE_NAME": "jp.penguin.purebase",
            "SOURCE_REPOSITORY": "PenguinDOOM/Pure-Base",
            "VERSION": VERSION,
            "TAG": VERSION,
            "COMMIT_SHA": COMMIT_SHA.upper(),
            "PACKAGE_URL": PACKAGE_URL,
            "EXPECTED_SHA256": DIGEST.upper(),
            "RELEASE_URL": RELEASE_URL,
        }
        payload = validate_payload(values)
        self.assertEqual(payload["commit_sha"], COMMIT_SHA)
        self.assertEqual(payload["expected_sha256"], DIGEST)

    def test_error_names_canonical_package_url_field(self) -> None:
        values = {
            "PACKAGE_NAME": "jp.penguin.purebase",
            "SOURCE_REPOSITORY": "PenguinDOOM/Pure-Base",
            "VERSION": VERSION,
            "TAG": VERSION,
            "COMMIT_SHA": COMMIT_SHA,
            "PACKAGE_URL": "https://example.invalid/package.zip",
            "EXPECTED_SHA256": DIGEST,
            "RELEASE_URL": RELEASE_URL,
        }
        with self.assertRaisesRegex(UpdateError, "packageUrl"):
            validate_payload(values)

    def test_resolves_annotated_tag_to_commit(self) -> None:
        tag_object_sha = "d" * 40
        responses = {
            f"https://api.github.com/repos/PenguinDOOM/Pure-Base/git/ref/tags/{VERSION}": {
                "object": {"type": "tag", "sha": tag_object_sha}
            },
            f"https://api.github.com/repos/PenguinDOOM/Pure-Base/git/tags/{tag_object_sha}": {
                "object": {"type": "commit", "sha": COMMIT_SHA}
            },
        }
        self.assertEqual(
            resolve_tag_commit(
                "PenguinDOOM/Pure-Base", VERSION, api_get=responses.__getitem__
            ),
            COMMIT_SHA,
        )

    def test_rejects_dispatch_commit_that_differs_from_tag(self) -> None:
        payload = valid_payload()

        def api_get(_: str) -> dict[str, object]:
            return {"object": {"type": "commit", "sha": OTHER_COMMIT_SHA}}

        with self.assertRaisesRegex(UpdateError, "not dispatched commit"):
            verify_release_commit(payload, api_get=api_get)


class ArchiveTests(unittest.TestCase):
    def test_rejects_zip_slip_paths(self) -> None:
        unsafe_paths = (
            "../package.json",
            "folder/../../package.json",
            "/package.json",
            "C:/package.json",
            "folder\\..\\package.json",
        )
        for path in unsafe_paths:
            with self.subTest(path=path):
                self.assertTrue(is_unsafe_zip_path(path))

    def test_rejects_sha256_mismatch(self) -> None:
        with self.assertRaisesRegex(UpdateError, "SHA-256 does not match"):
            verify_archive_sha256("0" * 64, DIGEST)

    def test_read_limited_checks_actual_decoded_size(self) -> None:
        oversized = io.BytesIO(b"x" * (MAX_PACKAGE_JSON_BYTES + 1))
        with self.assertRaisesRegex(UpdateError, "after decompression"):
            read_limited(oversized, MAX_PACKAGE_JSON_BYTES)

    def test_load_manifest_enriches_trusted_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "package.zip"
            write_zip(archive_path, json.dumps(valid_manifest()).encode("utf-8"))
            manifest = load_manifest(archive_path, valid_payload(), DIGEST)

        self.assertEqual(manifest["url"], PACKAGE_URL)
        self.assertEqual(manifest["zipSHA256"], DIGEST)
        self.assertEqual(manifest["changelogUrl"], RELEASE_URL)
        self.assertTrue(str(manifest["licensesUrl"]).endswith(f"{COMMIT_SHA}/LICENSE"))

    def test_load_manifest_rejects_missing_required_vpm_text(self) -> None:
        for field in ("displayName", "description", "unity"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary_directory:
                archive_path = Path(temporary_directory) / "package.zip"
                manifest = valid_manifest()
                manifest[field] = " "
                write_zip(archive_path, json.dumps(manifest).encode("utf-8"))
                with self.assertRaisesRegex(UpdateError, field):
                    load_manifest(archive_path, valid_payload(), DIGEST)

    def test_load_manifest_rejects_non_finite_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "package.zip"
            raw = json.dumps(valid_manifest())[:-1] + ', "bad": NaN}'
            write_zip(archive_path, raw.encode("utf-8"))
            with self.assertRaisesRegex(UpdateError, "strict UTF-8 JSON"):
                load_manifest(archive_path, valid_payload(), DIGEST)

    def test_load_manifest_rejects_unsafe_archive_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "package.zip"
            write_zip(
                archive_path,
                json.dumps(valid_manifest()).encode("utf-8"),
                extra_name="../escape.txt",
            )
            with self.assertRaisesRegex(UpdateError, "unsafe path"):
                load_manifest(archive_path, valid_payload(), DIGEST)


class ListingTests(unittest.TestCase):
    def test_adds_version_and_is_idempotent(self) -> None:
        listing: dict[str, object] = {"packages": {}}
        manifest = valid_manifest()
        manifest["url"] = PACKAGE_URL
        manifest["zipSHA256"] = DIGEST
        self.assertTrue(update_listing(listing, manifest))
        self.assertFalse(update_listing(listing, manifest.copy()))

    def test_rejects_overwriting_existing_version(self) -> None:
        manifest = valid_manifest()
        listing: dict[str, object] = {
            "packages": {
                "jp.penguin.purebase": {
                    "versions": {VERSION: manifest.copy()}
                }
            }
        }
        changed_manifest = manifest.copy()
        changed_manifest["description"] = "Changed"
        with self.assertRaisesRegex(UpdateError, "immutable"):
            update_listing(listing, changed_manifest)

    def test_listing_loader_and_writer_reject_non_finite_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "vpm.json"
            path.write_text('{"packages": {}, "bad": NaN}', encoding="utf-8")
            with self.assertRaisesRegex(UpdateError, "strict JSON"):
                load_listing(path)

            with self.assertRaisesRegex(UpdateError, "non-standard JSON"):
                write_listing(path, {"packages": {}, "bad": math.inf})


if __name__ == "__main__":
    unittest.main()
