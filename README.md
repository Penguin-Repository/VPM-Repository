# Penguin VPM Repository

VPM package listing for PenguinDOOM projects.

## Add the repository

Use this listing URL in VRChat Creator Companion or a compatible VPM client:

```text
https://raw.githubusercontent.com/PenguinDOOM/VPM-Repository/refs/heads/master/vpm.json
```

Alternatively, you can add it by pasting the following URL into your browser and opening it.
```text
vcc://vpm/addRepo?url=https://raw.githubusercontent.com/PenguinDOOM/VPM-Repository/refs/heads/master/vpm.json
```

## Pure Base release flow

`PenguinDOOM/Pure-Base` publishes an immutable GitHub Release and sends an `update-vpm` `repository_dispatch` event to this repository. The receiver then:

1. accepts only `jp.penguin.purebase` from `PenguinDOOM/Pure-Base`;
2. validates the version, tag, source commit, release URL, and asset URL;
3. downloads the release ZIP and verifies its SHA-256 against the dispatch payload;
4. validates the root `package.json` name, version, and Apache-2.0 license;
5. preserves package metadata such as `displayName`, `author`, `unity`, `description`, `vpmDependencies`, `keywords`, and `legacyFolders`;
6. adds the immutable package URL, `zipSHA256`, release-page `changelogUrl`, and commit-pinned `licensesUrl`;
7. appends a new immutable version to `vpm.json` and commits it.

A repeated dispatch for identical metadata is a no-op. A repeated version with different metadata fails instead of replacing a published version.

For each Pure Base version, generated links use:

- `changelogUrl`: the matching GitHub Release page;
- `licensesUrl`: `LICENSE` at the exact dispatched release commit.

This keeps historical metadata stable even if the default branch changes later.

## Required Pure Base configuration

Configure the following in `PenguinDOOM/Pure-Base`:

- Repository variable `VPM_REPOSITORY`: `PenguinDOOM/VPM-Repository`
- Release-environment secrets `APP_CLIENT_ID` and `APP_PRIVATE_KEY`
- A GitHub App installation that can write contents in both repositories

The receiving workflow uses its scoped `GITHUB_TOKEN` with `contents: write`; it does not need copies of the GitHub App private key.

## Dispatch payload

The `update-vpm` event uses this `client_payload` contract:

| Field | Meaning |
| --- | --- |
| `packageName` | `jp.penguin.purebase` |
| `version` | Stable semantic version |
| `tag` | Must equal `version` |
| `commitSha` | Source release commit SHA |
| `packageUrl` | Immutable release ZIP URL |
| `sha256` | SHA-256 of the release ZIP |
| `releaseUrl` | Published GitHub Release URL |
| `sourceRepository` | `PenguinDOOM/Pure-Base` |

`packageUrl` is the canonical field. The receiver temporarily accepts the legacy `packageurl` spelling so releases from the previous sender contract can still be reprocessed safely.

The workflow also exposes equivalent manual inputs for recovery or controlled reprocessing.
