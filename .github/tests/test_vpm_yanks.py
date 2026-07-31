"""Contract tests for Pure Base VPM Yank policy synchronization."""

from __future__ import annotations

import base64
import copy
import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"
sys.path.insert(0, str(SCRIPTS))

from vpm_common import UpdateError  # noqa: E402

PACKAGE_NAME = "jp.penguin.purebase"
SOURCE_REPOSITORY = "PenguinDOOM/Pure-Base"
POLICY_PATH = "vpm-yanks.json"
COMMIT_SHA = "a" * 40
CURRENT_COMMIT_SHA = "b" * 40
VERSION = "0.1.0-beta.1"


def policy_document(versions: dict[str, str] | None = None) -> bytes:
    """Return canonical UTF-8 policy bytes for a desired Yank state."""
    return json.dumps(
        {
            "schemaVersion": 1,
            "package": PACKAGE_NAME,
            "versions": {} if versions is None else versions,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def policy_document_of_size(size: int) -> bytes:
    """Return a valid policy whose encoded length is exactly size bytes."""
    prefix = policy_document({VERSION: ""})
    reason_length = size - len(prefix)
    if reason_length < 1:
        raise ValueError("Requested policy size is too small for the schema.")
    return policy_document({VERSION: "x" * reason_length})


def version_metadata(version: str, reason: str | None = None) -> dict[str, object]:
    """Build listing metadata with unrelated fields that must remain immutable."""
    metadata: dict[str, object] = {
        "name": PACKAGE_NAME,
        "version": version,
        "url": f"https://example.invalid/{version}.zip",
        "zipSHA256": "c" * 64,
        "description": "Immutable description",
        "license": "Apache-2.0",
        "vpmDependencies": {"jp.lilxyzw.shadercore": "0.1.5"},
        "vrc-get": {"note": "retain me", "yanked": "old reason"},
    }
    if reason is None:
        metadata["vrc-get"] = {"note": "retain me"}
    elif reason:
        metadata["vrc-get"] = {"note": "retain me", "yanked": reason}
    return metadata


def listing_with_versions(*versions: str) -> dict[str, object]:
    """Return a VPM listing fixture containing immutable package versions."""
    return {
        "packages": {
            PACKAGE_NAME: {
                "versions": {
                    version: version_metadata(version) for version in versions
                }
            }
        }
    }


class YankPolicySchemaTests(unittest.TestCase):
    def test_accepts_exactly_64_kib_policy(self) -> None:
        from vpm_policy import MAX_YANK_POLICY_BYTES, load_yank_policy

        raw = policy_document_of_size(64 * 1024)
        self.assertEqual(MAX_YANK_POLICY_BYTES, 64 * 1024)
        self.assertEqual(len(raw), MAX_YANK_POLICY_BYTES)
        self.assertEqual(load_yank_policy(raw)["versions"], {VERSION: "x" * (len(raw) - len(policy_document({VERSION: ""})))})

    def test_rejects_policy_larger_than_64_kib(self) -> None:
        from vpm_policy import MAX_YANK_POLICY_BYTES, load_yank_policy

        raw = policy_document_of_size(64 * 1024 + 1)
        self.assertEqual(len(raw), MAX_YANK_POLICY_BYTES + 1)
        with self.assertRaises(UpdateError):
            load_yank_policy(raw)

    def test_rejects_duplicate_keys_at_each_policy_nesting_level(self) -> None:
        from vpm_policy import load_yank_policy

        duplicate_documents = (
            (
                "top_level_schema_version",
                b'{"schemaVersion":1,"schemaVersion":1,"package":"jp.penguin.purebase","versions":{}}',
            ),
            (
                "versions_object_key",
                b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{"0.1.0":"first","0.1.0":"second"}}',
            ),
        )
        for contract_id, raw in duplicate_documents:
            with self.subTest(contract_id=contract_id), self.assertRaises(UpdateError):
                load_yank_policy(raw)

    def test_rejects_non_strict_json_policy_cases(self) -> None:
        from vpm_policy import load_yank_policy

        invalid_documents = (
            ("utf8_bom", b'\xef\xbb\xbf{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{}}'),
            ("invalid_utf8", b'\xff'),
            ("trailing_content", b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{}} trailing'),
            ("trailing_whitespace", b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{}}\n'),
            ("nan_constant", b'{"schemaVersion":NaN,"package":"jp.penguin.purebase","versions":{}}'),
            ("infinity_constant", b'{"schemaVersion":Infinity,"package":"jp.penguin.purebase","versions":{}}'),
        )
        for contract_id, raw in invalid_documents:
            with self.subTest(contract_id=contract_id), self.assertRaises(UpdateError):
                load_yank_policy(raw)

    def test_rejects_missing_required_schema_version_key(self) -> None:
        from vpm_policy import load_yank_policy

        with self.assertRaises(UpdateError):
            load_yank_policy(b'{"package":"jp.penguin.purebase","versions":{}}')

    def test_rejects_missing_required_package_key(self) -> None:
        from vpm_policy import load_yank_policy

        with self.assertRaises(UpdateError):
            load_yank_policy(b'{"schemaVersion":1,"versions":{}}')

    def test_rejects_missing_required_versions_key(self) -> None:
        from vpm_policy import load_yank_policy

        with self.assertRaises(UpdateError):
            load_yank_policy(b'{"schemaVersion":1,"package":"jp.penguin.purebase"}')

    def test_rejects_invalid_schema_policy_cases(self) -> None:
        from vpm_policy import load_yank_policy

        invalid_documents = (
            ("boolean_schema_version", b'{"schemaVersion":true,"package":"jp.penguin.purebase","versions":{}}'),
            ("string_schema_version", b'{"schemaVersion":"1","package":"jp.penguin.purebase","versions":{}}'),
            ("wrong_package", b'{"schemaVersion":1,"package":"other.package","versions":{}}'),
            ("unexpected_top_level_key", b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{},"extra":true}'),
            ("non_object_versions", b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":[]}'),
            ("invalid_semver_version_key", b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{"v0.1.0":"reason"}}'),
            ("empty_reason", b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{"0.1.0":""}}'),
            ("whitespace_reason", b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{"0.1.0":"   "}}'),
            ("non_string_reason", b'{"schemaVersion":1,"package":"jp.penguin.purebase","versions":{"0.1.0":true}}'),
        )
        for contract_id, raw in invalid_documents:
            with self.subTest(contract_id=contract_id), self.assertRaises(UpdateError):
                load_yank_policy(raw)


class YankPolicyFetchTests(unittest.TestCase):
    def assert_sync_rejection_leaves_listing_unchanged(self, error: UpdateError) -> None:
        """Assert a rejected policy sync never persists a candidate listing."""
        import sync_vpm_yanks

        with tempfile.TemporaryDirectory() as temporary_directory:
            listing_path = Path(temporary_directory) / "vpm.json"
            original = json.dumps(listing_with_versions(VERSION))
            listing_path.write_text(original, encoding="utf-8")
            environment = {
                "PACKAGE_NAME": PACKAGE_NAME,
                "SOURCE_REPOSITORY": SOURCE_REPOSITORY,
                "POLICY_COMMIT_SHA": COMMIT_SHA,
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(sync_vpm_yanks, "VPM_PATH", listing_path),
                patch.object(
                    sync_vpm_yanks,
                    "fetch_yank_policy_snapshot",
                    side_effect=error,
                ),
            ):
                with self.assertRaises(UpdateError):
                    sync_vpm_yanks.process_yank_sync()

            self.assertEqual(listing_path.read_text(encoding="utf-8"), original)

    def test_fetch_uses_fixed_repository_branch_and_path(self) -> None:
        from vpm_policy import YANK_POLICY_PATH, fetch_yank_policy_snapshot

        parameters = inspect.signature(fetch_yank_policy_snapshot).parameters
        self.assertEqual(YANK_POLICY_PATH, POLICY_PATH)
        self.assertNotIn("path", parameters)
        self.assertNotIn("repository", parameters)
        self.assertNotIn("branch", parameters)

    def test_accepts_line_wrapped_github_base64_policy_content(self) -> None:
        from vpm_policy import fetch_yank_policy_snapshot

        raw = policy_document({VERSION: "same desired state"})
        encoded = base64.b64encode(raw).decode("ascii")
        line_wrapped = "\r\n".join(
            encoded[offset : offset + 60] for offset in range(0, len(encoded), 60)
        )

        def api_get(url: str) -> dict[str, object]:
            if "/compare/" in url:
                return {"status": "ahead"}
            if f"ref={COMMIT_SHA}" in url or "ref=master" in url:
                return {"content": line_wrapped, "encoding": "base64"}
            return {"sha": CURRENT_COMMIT_SHA}

        policy = fetch_yank_policy_snapshot(COMMIT_SHA, api_get=api_get)

        self.assertEqual(policy["versions"], {VERSION: "same desired state"})

    def test_rejects_base64_content_with_non_api_whitespace_characters(self) -> None:
        from vpm_policy import decode_policy_content

        encoded = base64.b64encode(policy_document()).decode("ascii")
        invalid_contents = (
            encoded[:8] + "@" + encoded[8:],
            encoded[:8] + "\t" + encoded[8:],
        )

        for content in invalid_contents:
            with self.subTest(content=content), self.assertRaises(UpdateError):
                decode_policy_content({"content": content, "encoding": "base64"}, COMMIT_SHA)

    def test_rejects_invalid_policy_commit_before_github_api_access(self) -> None:
        from vpm_policy import fetch_yank_policy_snapshot

        def api_get(_: str) -> dict[str, object]:
            self.fail("Invalid policy SHA must be rejected before API access.")

        with self.assertRaises(UpdateError):
            fetch_yank_policy_snapshot("not-a-commit", api_get=api_get)

    def test_rejects_policy_commit_not_reachable_from_master(self) -> None:
        from vpm_policy import fetch_yank_policy_snapshot

        def api_get(url: str) -> dict[str, object]:
            if "/compare/" in url:
                return {"status": "diverged"}
            return {"sha": COMMIT_SHA}

        with self.assertRaises(UpdateError):
            fetch_yank_policy_snapshot(COMMIT_SHA, api_get=api_get)

    def test_rejects_stale_current_policy_snapshot_content(self) -> None:
        from vpm_policy import fetch_yank_policy_snapshot

        requested = policy_document({VERSION: "requested reason"})
        current = policy_document({VERSION: "current reason"})

        def api_get(url: str) -> dict[str, object]:
            if "/compare/" in url:
                return {"status": "ahead"}
            if f"ref={COMMIT_SHA}" in url:
                return {"content": base64.b64encode(requested).decode("ascii"), "encoding": "base64"}
            if "ref=master" in url:
                return {"content": base64.b64encode(current).decode("ascii"), "encoding": "base64"}
            return {"sha": CURRENT_COMMIT_SHA}

        with self.assertRaises(UpdateError):
            fetch_yank_policy_snapshot(COMMIT_SHA, api_get=api_get)

    def test_accepts_old_reachable_snapshot_when_current_policy_content_is_identical(self) -> None:
        from vpm_policy import fetch_yank_policy_snapshot

        raw = policy_document({VERSION: "same desired state"})

        def api_get(url: str) -> dict[str, object]:
            if "/compare/" in url:
                return {"status": "ahead"}
            if f"ref={COMMIT_SHA}" in url or "ref=master" in url:
                return {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"}
            return {"sha": CURRENT_COMMIT_SHA}

        policy = fetch_yank_policy_snapshot(COMMIT_SHA, api_get=api_get)
        self.assertEqual(policy["versions"], {VERSION: "same desired state"})

    def test_stale_policy_snapshot_rejection_leaves_listing_byte_identical(self) -> None:
        self.assert_sync_rejection_leaves_listing_unchanged(
            UpdateError("Policy snapshot no longer matches current master policy")
        )

    def test_github_api_failure_leaves_listing_byte_identical(self) -> None:
        self.assert_sync_rejection_leaves_listing_unchanged(
            UpdateError("GitHub API request failed")
        )

    def test_sync_result_exposes_verified_policy_commit_sha(self) -> None:
        import sync_vpm_yanks

        with tempfile.TemporaryDirectory() as temporary_directory:
            listing_path = Path(temporary_directory) / "vpm.json"
            listing_path.write_text(
                json.dumps(listing_with_versions(VERSION)), encoding="utf-8"
            )
            environment = {
                "PACKAGE_NAME": PACKAGE_NAME,
                "SOURCE_REPOSITORY": SOURCE_REPOSITORY,
                "POLICY_COMMIT_SHA": COMMIT_SHA.upper(),
            }
            policy = policy_document({VERSION: "reason"})
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(sync_vpm_yanks, "VPM_PATH", listing_path),
                patch.object(
                    sync_vpm_yanks,
                    "fetch_yank_policy_snapshot",
                    return_value=json.loads(policy),
                ),
            ):
                _, policy_commit_sha, changed = sync_vpm_yanks.process_yank_sync()

        self.assertEqual(policy_commit_sha, COMMIT_SHA)
        self.assertTrue(changed)

    def test_sync_main_outputs_verified_policy_commit_sha(self) -> None:
        import sync_vpm_yanks

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            listing_path = temporary_path / "vpm.json"
            output_path = temporary_path / "output"
            summary_path = temporary_path / "summary"
            listing_path.write_text(
                json.dumps(listing_with_versions(VERSION)), encoding="utf-8"
            )
            environment = {
                "PACKAGE_NAME": PACKAGE_NAME,
                "SOURCE_REPOSITORY": SOURCE_REPOSITORY,
                "POLICY_COMMIT_SHA": COMMIT_SHA.upper(),
                "GITHUB_OUTPUT": str(output_path),
                "GITHUB_STEP_SUMMARY": str(summary_path),
            }
            policy = policy_document({VERSION: "reason"})
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(sync_vpm_yanks, "VPM_PATH", listing_path),
                patch.object(
                    sync_vpm_yanks,
                    "fetch_yank_policy_snapshot",
                    return_value=json.loads(policy),
                ),
            ):
                self.assertEqual(sync_vpm_yanks.main(), 0)

            self.assertIn(
                f"policy_commit_sha={COMMIT_SHA}\n",
                output_path.read_text(encoding="utf-8"),
            )
            self.assertIn(
                f"- Policy commit: `{COMMIT_SHA}`",
                summary_path.read_text(encoding="utf-8"),
            )


class YankApplicationTests(unittest.TestCase):
    def test_yank_mutation_is_limited_to_allowed_vrc_get_yanked_path(self) -> None:
        from vpm_listing import apply_yank_policy

        listing = listing_with_versions(VERSION)
        before = copy.deepcopy(listing)
        policy = {"schemaVersion": 1, "package": PACKAGE_NAME, "versions": {VERSION: "new reason"}}

        self.assertTrue(apply_yank_policy(listing, policy))

        expected = copy.deepcopy(before)
        expected["packages"][PACKAGE_NAME]["versions"][VERSION]["vrc-get"]["yanked"] = "new reason"
        self.assertEqual(listing, expected)

    def test_unyank_removes_only_yanked_and_keeps_other_vrc_get_fields(self) -> None:
        from vpm_listing import apply_yank_policy

        listing = listing_with_versions(VERSION)
        listing["packages"][PACKAGE_NAME]["versions"][VERSION]["vrc-get"][
            "yanked"
        ] = "old reason"
        self.assertTrue(apply_yank_policy(listing, {"schemaVersion": 1, "package": PACKAGE_NAME, "versions": {}}))
        self.assertEqual(
            listing["packages"][PACKAGE_NAME]["versions"][VERSION]["vrc-get"],
            {"note": "retain me"},
        )

    def test_full_clear_removes_empty_vrc_get_object(self) -> None:
        from vpm_listing import apply_yank_policy

        listing = listing_with_versions(VERSION)
        listing["packages"][PACKAGE_NAME]["versions"][VERSION]["vrc-get"] = {"yanked": "old reason"}

        self.assertTrue(apply_yank_policy(listing, {"schemaVersion": 1, "package": PACKAGE_NAME, "versions": {}}))

        self.assertNotIn("vrc-get", listing["packages"][PACKAGE_NAME]["versions"][VERSION])

    def test_rejects_non_object_vrc_get_without_mutating_listing(self) -> None:
        from vpm_listing import apply_yank_policy

        listing = listing_with_versions(VERSION)
        listing["packages"][PACKAGE_NAME]["versions"][VERSION]["vrc-get"] = "not an object"
        before = copy.deepcopy(listing)

        with self.assertRaises(UpdateError):
            apply_yank_policy(
                listing,
                {"schemaVersion": 1, "package": PACKAGE_NAME, "versions": {VERSION: "reason"}},
            )

        self.assertEqual(listing, before)

    def test_rejects_unknown_versions_without_mutating_listing(self) -> None:
        from vpm_listing import apply_yank_policy

        listing = listing_with_versions(VERSION)
        before = copy.deepcopy(listing)
        unknown_policy = {
            "schemaVersion": 1,
            "package": PACKAGE_NAME,
            "versions": {"0.1.0": "not listed"},
        }

        with self.assertRaises(UpdateError):
            apply_yank_policy(listing, unknown_policy)

        self.assertEqual(listing, before)

    def test_reapplying_identical_policy_is_a_no_op(self) -> None:
        from vpm_listing import apply_yank_policy

        listing = listing_with_versions(VERSION, "0.1.0")
        policy = {"schemaVersion": 1, "package": PACKAGE_NAME, "versions": {VERSION: "reason"}}

        self.assertTrue(apply_yank_policy(listing, policy))
        before = copy.deepcopy(listing)
        self.assertFalse(apply_yank_policy(listing, policy))
        self.assertEqual(listing, before)


class YankWorkflowContractTests(unittest.TestCase):
    def test_receiver_workflow_runs_yank_contract_suite_on_relevant_changes(self) -> None:
        workflow = (WORKFLOWS / "receiver-tests.yml").read_text(encoding="utf-8")

        self.assertIn(".github/scripts/**", workflow)
        self.assertIn(".github/tests/**", workflow)
        self.assertIn(".github/workflows/sync-vpm-yanks.yml", workflow)
        self.assertIn("contents: read", workflow)

    def assert_workflow_uses_shared_update_serialization(self, filename: str) -> None:
        """Assert one named VPM writer serializes updates with the shared concurrency contract."""
        workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
        concurrency = "concurrency:\n  group: vpm-repository-update\n  cancel-in-progress: false"

        self.assertIn(concurrency, workflow)

    def test_update_workflow_uses_shared_update_serialization(self) -> None:
        self.assert_workflow_uses_shared_update_serialization("update-vpm.yml")

    def test_yank_workflow_uses_shared_update_serialization(self) -> None:
        self.assert_workflow_uses_shared_update_serialization("sync-vpm-yanks.yml")

    def assert_mutating_workflow_is_fixed_to_master(self, filename: str) -> None:
        """Assert one named VPM writer cannot mutate a repository with another default branch."""
        workflow = (WORKFLOWS / filename).read_text(encoding="utf-8")
        self.assertIn("ref: master", workflow)
        self.assertIn('git push origin "HEAD:master"', workflow)
        self.assertIn('github.event.repository.default_branch != \'master\'', workflow)
        self.assertIn("contents: write", workflow)

    def test_update_workflow_is_fixed_to_master_and_rejects_other_defaults(self) -> None:
        self.assert_mutating_workflow_is_fixed_to_master("update-vpm.yml")

    def test_yank_workflow_is_fixed_to_master_and_rejects_other_defaults(self) -> None:
        self.assert_mutating_workflow_is_fixed_to_master("sync-vpm-yanks.yml")

    def test_update_workflow_accepts_only_release_event_and_manual_policy_commit(self) -> None:
        workflow = (WORKFLOWS / "update-vpm.yml").read_text(encoding="utf-8")

        self.assertIn("types: [update-vpm]", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("policy_commit_sha:", workflow)
        self.assertNotIn("policy_path", workflow)
        self.assertNotIn("policyPath", workflow)

    def test_yank_workflow_accepts_only_yank_event_and_manual_fixed_source_inputs(self) -> None:
        workflow = (WORKFLOWS / "sync-vpm-yanks.yml").read_text(encoding="utf-8")

        self.assertIn("types: [sync-vpm-yanks]", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("package_name:", workflow)
        self.assertIn("source_repository:", workflow)
        self.assertIn("policy_commit_sha:", workflow)
        self.assertNotIn("policy_path", workflow)
        self.assertNotIn("policyPath", workflow)


if __name__ == "__main__":
    unittest.main()