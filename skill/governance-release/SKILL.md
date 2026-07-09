---
name: governance-release
description: Cut a versioned release PR for VEMO — collect merged PRs since the last tag, bump VERSION, update CHANGELOG/CONTRIBUTORS, run docs-sync, open the PR. The maintainer merges and tags. Use when preparing a release.
---

# Governance Release

Package merged work into a tagged version. Creates a **PR only** — never merges or tags. **Manual only.**

**Prereq** — `gh auth status`.
**When** — manual: "cut a release", "prepare a release", "publish a new version".

**Steps**
1. Compare the latest tag to HEAD; if equal → "nothing to release", stop.
2. Collect merged PRs since the tag (titles + `Co-Authored-By` contributors).
3. Suggest a semver bump (patch / minor / major) from PR content; user can override.
4. On a `release/vX.Y.Z` branch: bump `VERSION` + `vemo.config.yaml` version, write the `CHANGELOG` entry,
   update `CONTRIBUTORS`, then run **`docs-sync`** (version line + any capability changes).
5. Commit (`Co-Authored-By`), push, `gh pr create` to the default branch.
6. Print the tag command for the maintainer (tag the **merge commit**, not the release branch); clean temp.

**Notes** — PR-only; only the maintainer merges + tags. Self-governance: this repo's own hooks/CI run on the
release PR, so a release is held to the same gates as any other change.
