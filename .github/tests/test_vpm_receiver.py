"""Unit tests for the Pure Base VPM dispatch receiver."""

from __future__ import annotations

import copy
import io
import json
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

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
from vpm_listing import apply_yank_policy, load_listing, update_listing, write_listing  # noqa: E402
from vpm_payload import resolve_tag_commit, validate_payload, verify_release_commit  # noqa: E402

COMMIT_SHA = "a" * 40
OTHER_COMMIT_SHA = "b" * 40
DIGEST = "c" * 64
VERSION = "0.2.0"
PACKAGE_URL = (
    "https://github.com/Penguin-Repository/Pure-Base/releases/download/"
    f"{VERSION}/jp.penguin.purebase-{VERSION}.zip"
)
RELEASE_URL = f"https://github.com/Penguin-Repository/Pure-Base/releases/tag/{VERSION}"


def valid_payload(
    version: str = VERSION,
    policy_commit_sha: str = COMMIT_SHA,
) -> dict[str, str]:
    asset_name = f"jp.penguin.purebase-{version}.zip"
    package_url = (
        "https://github.com/Penguin-Repository/Pure-Base/releases/download/"
        f"{version}/{asset_name}"
    )
    release_url = f"https://github.com/Penguin-Repository/Pure-Base/releases/tag/{version}"
    return {
        "package_name": "jp.penguin.purebase",
        "source_repository": "Penguin-Repository/Pure-Base",
        "version": version,
        "commit_sha": COMMIT_SHA,
        "policy_commit_sha": policy_commit_sha,
        "asset_name": asset_name,
        "package_url": package_url,
        "expected_sha256": DIGEST,
        "release_url": release_url,
        "changelog_url": release_url,
        "licenses_url": (
            "https://github.com/Penguin-Repository/Pure-Base/blob/"
            f"{COMMIT_SHA}/LICENSE"
        ),
    }


def valid_manifest(version: str = VERSION) -> dict[str, object]:
    return {
        "name": "jp.penguin.purebase",
        "displayName": "PureBase",
        "version": version,
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


class SemVerTests(unittest.TestCase):
    def test_accepts_shared_semver_vectors(self) -> None:
        from vpm_common import version_key

        accepted = (
            ("prerelease_alpha_1", "0.1.0-alpha.1"),
            ("prerelease_beta_1", "0.1.0-beta.1"),
            ("prerelease_beta_2", "0.1.0-beta.2"),
            ("prerelease_rc_1", "0.1.0-rc.1"),
            ("stable_release", "0.1.0"),
        )
        for contract_id, version in accepted:
            with self.subTest(contract_id=contract_id):
                self.assertIsNotNone(version_key(version))

    def test_rejects_shared_semver_vectors(self) -> None:
        from vpm_common import version_key

        rejected = (
            ("v_prefix", "v0.1.0"),
            ("leading_zero_core", "01.0.0"),
            ("missing_patch", "0.1"),
            ("empty_prerelease", "0.1.0-"),
            ("empty_prerelease_identifier", "0.1.0-beta..1"),
            ("leading_zero_numeric_prerelease", "0.1.0-01"),
            ("build_metadata", "0.1.0+build.1"),
            ("prerelease_build_metadata", "0.1.0-beta.1+build.1"),
            ("non_ascii_prerelease", "0.1.0-beta.\u00e4"),
        )
        for contract_id, version in rejected:
            with self.subTest(contract_id=contract_id), self.assertRaises(UpdateError):
                version_key(version)

    def test_rejects_long_malformed_prerelease(self) -> None:
        from vpm_common import version_key

        with self.assertRaises(UpdateError):
            version_key("0.1.0-" + "a." * 20_000)

    def test_orders_shared_semver_vectors(self) -> None:
        from vpm_common import version_key

        ordered = (
            ("alpha_before_alpha_dot_1", "0.1.0-alpha", "0.1.0-alpha.1"),
            ("alpha_dot_1_before_alpha_dot_beta", "0.1.0-alpha.1", "0.1.0-alpha.beta"),
            ("alpha_dot_beta_before_beta", "0.1.0-alpha.beta", "0.1.0-beta"),
            ("beta_before_beta_dot_2", "0.1.0-beta", "0.1.0-beta.2"),
            ("beta_dot_2_before_beta_dot_11", "0.1.0-beta.2", "0.1.0-beta.11"),
            ("beta_dot_11_before_rc_dot_1", "0.1.0-beta.11", "0.1.0-rc.1"),
            ("rc_dot_1_before_stable", "0.1.0-rc.1", "0.1.0"),
        )
        for contract_id, lower, higher in ordered:
            with self.subTest(contract_id=contract_id):
                self.assertLess(version_key(lower), version_key(higher))

    def test_orders_shared_unbounded_numeric_vectors(self) -> None:
        from vpm_common import version_key

        ordered = (
            (
                "unbounded_core_component",
                "18446744073709551615.999.999",
                "18446744073709551616.0.0",
            ),
            (
                "unbounded_numeric_prerelease_identifier",
                "0.1.0-beta.18446744073709551615",
                "0.1.0-beta.18446744073709551616",
            ),
        )
        for contract_id, lower, higher in ordered:
            with self.subTest(contract_id=contract_id):
                self.assertLess(version_key(lower), version_key(higher))


class PayloadTests(unittest.TestCase):
    def test_accepts_canonical_payload(self) -> None:
        values = {
            "PACKAGE_NAME": "jp.penguin.purebase",
            "SOURCE_REPOSITORY": "Penguin-Repository/Pure-Base",
            "VERSION": VERSION,
            "TAG": VERSION,
            "COMMIT_SHA": COMMIT_SHA.upper(),
            "POLICY_COMMIT_SHA": COMMIT_SHA.upper(),
            "PACKAGE_URL": PACKAGE_URL,
            "EXPECTED_SHA256": DIGEST.upper(),
            "RELEASE_URL": RELEASE_URL,
        }
        payload = validate_payload(values)
        self.assertEqual(payload["commit_sha"], COMMIT_SHA)
        self.assertEqual(payload["expected_sha256"], DIGEST)

    def test_accepts_prerelease_payload_with_separate_policy_commit(self) -> None:
        prerelease = "0.1.0-beta.1"
        payload = valid_payload(prerelease, OTHER_COMMIT_SHA)
        values = {
            "PACKAGE_NAME": payload["package_name"],
            "SOURCE_REPOSITORY": payload["source_repository"],
            "VERSION": payload["version"],
            "TAG": payload["version"],
            "COMMIT_SHA": payload["commit_sha"],
            "POLICY_COMMIT_SHA": payload["policy_commit_sha"],
            "PACKAGE_URL": payload["package_url"],
            "EXPECTED_SHA256": payload["expected_sha256"],
            "RELEASE_URL": payload["release_url"],
        }

        validated = validate_payload(values)

        self.assertEqual(validated["version"], prerelease)
        self.assertEqual(validated["asset_name"], f"jp.penguin.purebase-{prerelease}.zip")
        self.assertEqual(validated["policy_commit_sha"], OTHER_COMMIT_SHA)

    def test_error_names_canonical_package_url_field(self) -> None:
        values = {
            "PACKAGE_NAME": "jp.penguin.purebase",
            "SOURCE_REPOSITORY": "Penguin-Repository/Pure-Base",
            "VERSION": VERSION,
            "TAG": VERSION,
            "COMMIT_SHA": COMMIT_SHA,
            "POLICY_COMMIT_SHA": COMMIT_SHA,
            "PACKAGE_URL": "https://example.invalid/package.zip",
            "EXPECTED_SHA256": DIGEST,
            "RELEASE_URL": RELEASE_URL,
        }
        with self.assertRaisesRegex(UpdateError, "packageUrl"):
            validate_payload(values)

    def test_resolves_annotated_tag_to_commit(self) -> None:
        tag_object_sha = "d" * 40
        responses = {
            f"https://api.github.com/repos/Penguin-Repository/Pure-Base/git/ref/tags/{VERSION}": {
                "object": {"type": "tag", "sha": tag_object_sha}
            },
            f"https://api.github.com/repos/Penguin-Repository/Pure-Base/git/tags/{tag_object_sha}": {
                "object": {"type": "commit", "sha": COMMIT_SHA}
            },
        }
        self.assertEqual(
            resolve_tag_commit(
                "Penguin-Repository/Pure-Base", VERSION, api_get=responses.__getitem__
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

    def test_allows_release_replay_after_yank_policy_projection(self) -> None:
        listing: dict[str, object] = {"packages": {}}
        manifest = valid_manifest()

        self.assertTrue(update_listing(listing, manifest.copy()))
        self.assertTrue(
            apply_yank_policy(
                listing,
                {
                    "schemaVersion": 1,
                    "package": "jp.penguin.purebase",
                    "versions": {VERSION: "withdrawn"},
                },
            )
        )
        projected_listing = copy.deepcopy(listing)

        self.assertFalse(update_listing(listing, manifest.copy()))
        self.assertEqual(listing, projected_listing)

    def test_rejects_invalid_existing_version_value_without_mutation(self) -> None:
        manifest = valid_manifest()
        for invalid_value in (None, "not an object"):
            with self.subTest(invalid_value=invalid_value):
                listing: dict[str, object] = {
                    "packages": {
                        "jp.penguin.purebase": {
                            "versions": {VERSION: invalid_value}
                        }
                    }
                }
                before = copy.deepcopy(listing)

                with self.assertRaisesRegex(UpdateError, "metadata is not an object"):
                    update_listing(listing, manifest)

                self.assertEqual(listing, before)

    def test_rejects_invalid_existing_package_value_without_mutation(self) -> None:
        manifest = valid_manifest()
        for invalid_value in (None, "not an object"):
            with self.subTest(invalid_value=invalid_value):
                listing: dict[str, object] = {
                    "packages": {"jp.penguin.purebase": invalid_value}
                }
                before = copy.deepcopy(listing)

                with self.assertRaisesRegex(UpdateError, "Package entry .* is not an object"):
                    update_listing(listing, manifest)

                self.assertEqual(listing, before)

    def test_inserts_versions_in_descending_semver_order(self) -> None:
        listing: dict[str, object] = {"packages": {}}
        versions = (
            "0.1.0-beta.2",
            "0.1.0",
            "0.1.0-alpha.1",
            "0.1.0-rc.1",
            "0.1.0-beta.11",
        )
        for version in versions:
            self.assertTrue(update_listing(listing, valid_manifest(version)))

        package = listing["packages"]["jp.penguin.purebase"]
        self.assertEqual(
            list(package["versions"]),
            [
                "0.1.0",
                "0.1.0-rc.1",
                "0.1.0-beta.11",
                "0.1.0-beta.2",
                "0.1.0-alpha.1",
            ],
        )

    def test_listing_loader_and_writer_reject_non_finite_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "vpm.json"
            path.write_text('{"packages": {}, "bad": NaN}', encoding="utf-8")
            with self.assertRaisesRegex(UpdateError, "strict JSON"):
                load_listing(path)

            with self.assertRaisesRegex(UpdateError, "non-standard JSON"):
                write_listing(path, {"packages": {}, "bad": math.inf})


class UpdateTransactionTests(unittest.TestCase):
    def test_policy_failure_prevents_archive_or_listing_access(self) -> None:
        import update_vpm

        with tempfile.TemporaryDirectory() as temporary_directory:
            listing_path = Path(temporary_directory) / "vpm.json"
            original = '{"packages": {}}\n'
            listing_path.write_text(original, encoding="utf-8")
            with (
                patch.object(update_vpm, "VPM_PATH", listing_path),
                patch.object(update_vpm, "validate_payload", return_value=valid_payload()),
                patch.object(update_vpm, "verify_release_commit"),
                patch.object(
                    update_vpm,
                    "fetch_yank_policy_snapshot",
                    side_effect=UpdateError("Policy snapshot no longer matches current master policy"),
                ),
                patch.object(
                    update_vpm,
                    "download_archive",
                    side_effect=AssertionError("Archive download must not start."),
                ),
                patch.object(
                    update_vpm,
                    "load_listing",
                    side_effect=AssertionError("Listing must not be loaded."),
                ),
            ):
                with self.assertRaises(UpdateError):
                    update_vpm.process_update()

            self.assertEqual(listing_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
