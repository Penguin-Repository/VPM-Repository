# Penguin VPM Repository

VPM package listing for Penguin-Repository projects.

## Add the repository

Use this listing URL in VRChat Creator Companion or a compatible VPM client:

```text
https://raw.githubusercontent.com/Penguin-Repository/VPM-Repository/refs/heads/master/vpm.json
```

Alternatively, you can add it by pasting the following URL into your browser and opening it.
```text
vcc://vpm/addRepo?url=https://raw.githubusercontent.com/Penguin-Repository/VPM-Repository/refs/heads/master/vpm.json
```

Pure Base uses two separate receiver dispatches: `update-vpm` adds a published package release, while `sync-vpm-yanks` projects the fixed yank policy without release or archive processing.

## Pure Base release flow

`Penguin-Repository/Pure-Base` publishes an immutable GitHub Release and sends an `update-vpm` `repository_dispatch` event to this repository. The receiver then:

1. accepts only `jp.penguin.purebase` from `Penguin-Repository/Pure-Base`;
2. validates the version, tag, source commit, release URL, and asset URL;
3. downloads the release ZIP and verifies its SHA-256 against the dispatch payload;
4. validates the root `package.json` name, version, and Apache-2.0 license;
5. preserves package metadata such as `displayName`, `author`, `unity`, `description`, `vpmDependencies`, `keywords`, and `legacyFolders`;
6. adds the immutable package URL, `zipSHA256`, release-page `changelogUrl`, and commit-pinned `licensesUrl`;
7. appends a new immutable version to `vpm.json` and commits it.

A repeated dispatch for identical metadata is a no-op. A repeated version with different metadata fails instead of replacing a published version.

Prerelease versions are valid listing entries and are kept in `vpm.json` alongside stable versions. VPM clients decide whether prereleases are visible by default; clients that hide prereleases require their prerelease or development-version setting to be enabled. The release receiver does not silently discard a valid prerelease.

The VPM writer workflows only mutate the literal `master` branch. They stop before checkout when the repository default branch is not `master`, and a non-fast-forward push fails without force-push or rebase behavior.

For each Pure Base version, generated links use:

- `changelogUrl`: the matching GitHub Release page;
- `licensesUrl`: `LICENSE` at the exact dispatched release commit.

This keeps historical metadata stable even if the default branch changes later.

## Required GitHub App configuration

Configure the following in both `Penguin-Repository/Pure-Base` and `Penguin-Repository/VPM-Repository`:

- Repository or environment secrets `APP_CLIENT_ID` and `APP_PRIVATE_KEY`
- A GitHub App installation covering both repositories
- GitHub App repository permission `Contents: Read and write`

Configure repository variable `VPM_REPOSITORY` in `Penguin-Repository/Pure-Base` as `Penguin-Repository/VPM-Repository`.

The receiving workflows create a repository-scoped installation token with `contents: write`, use it for checkout and direct pushes to `master`, and commit as the GitHub App bot. The workflow-level `GITHUB_TOKEN` remains read-only and is used only for validation requests.

## Dispatch payload

The `update-vpm` event uses this `client_payload` contract:

| Field | Meaning |
| --- | --- |
| `packageName` | `jp.penguin.purebase` |
| `version` | Strict SemVer core with an optional prerelease; build metadata is rejected |
| `tag` | Must equal `version` |
| `commitSha` | Source release commit SHA |
| `packageUrl` | Immutable release ZIP URL |
| `sha256` | SHA-256 of the release ZIP |
| `releaseUrl` | Published GitHub Release URL |
| `sourceRepository` | `Penguin-Repository/Pure-Base` |
| `policyCommitSha` | Commit SHA for the Pure Base `vpm-yanks.json` policy |

`packageUrl` is the canonical field. The receiver temporarily accepts the legacy `packageurl` spelling so releases from the previous sender contract can still be reprocessed safely.

The workflow also exposes equivalent manual inputs for recovery or controlled reprocessing, including `policy_commit_sha`. The source repository and package name are validated against the fixed Pure Base values; no dispatch field is used as a checkout path.

## Yank synchronization

The `sync-vpm-yanks` `repository_dispatch` event is policy-only. It reads only `vpm-yanks.json` from the fixed `Penguin-Repository/Pure-Base` `master` branch and calls the policy synchronization entrypoint; it does not download a release archive or add release metadata.

Its `client_payload` contract is:

| Field | Meaning |
| --- | --- |
| `packageName` | `jp.penguin.purebase` |
| `sourceRepository` | `Penguin-Repository/Pure-Base` |
| `policyCommitSha` | 40-character commit SHA for the policy snapshot |

Manual recovery uses the constrained inputs `package_name`, `source_repository`, and `policy_commit_sha`. The policy path (`vpm-yanks.json`) and policy branch (`master`) are fixed in the receiver and cannot be supplied by the event or a manual run.

The policy is a desired state. A version present in `versions` receives `vrc-get.yanked` with its public reason. A listed version absent from `versions` is un-Yanked, removing only that marker and retaining other metadata. The policy reason is published in `vpm.json`; workflow summaries expose only the package, policy commit, count of yanked versions, and result, not the reason. The policy reason must never contain secrets, credentials, private data, or personally identifiable information.

The receiver verifies that the requested policy commit is reachable from Pure Base `master` and that its policy bytes still match current `master`. A stale retry fails closed without changing `vpm.json`; retry with a current policy commit SHA. An older reachable SHA is accepted only while the policy file content remains identical.

ALCOM-specific limitation: ALCOM may not surface or enforce the `vrc-get.yanked` marker and may hide prerelease versions according to its own UI and resolver behavior. The listing and policy remain authoritative for clients that support these fields; in ALCOM, users may need to avoid a yanked version manually or use a client that exposes prerelease and yank state.
