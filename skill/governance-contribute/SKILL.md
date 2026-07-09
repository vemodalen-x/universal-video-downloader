---
name: governance-contribute
description: Contribute a framework improvement from a consuming project back to the VEMO upstream via pull request. Use when you've improved a template-owned spec / skill / enforcement file and want it upstream.
---

# Governance Contribute

Fork → branch → copy the changed template-owned files → PR. **Manual only.**

**Prereq** — `gh auth status`.
**When** — manual: "contribute upstream", "push this improvement back", "send this improvement upstream".

**Steps**
1. Diff local **template-owned** files against the upstream → list what changed.
2. User selects which files to contribute (don't ship unrelated local tweaks).
3. `gh repo fork <upstream>` (if not already) → clone to a temp dir → branch `contrib/<project>-<summary>` off upstream HEAD.
4. Copy the selected files; draft a `CHANGELOG` entry under `[Unreleased]`; bump VERSION (patch by default).
5. Commit with a `Co-Authored-By:` trailer; push; `gh pr create` targeting the upstream default branch.
6. If capabilities changed, run `docs-sync` and include it in the same PR.
7. Remove the temp dir.

**Notes** — only template-owned files (never instance config, runtime, or `AGENTS.md`). It's always a PR, never
an auto-merge. `Co-Authored-By` is mandatory so contribution is attributable.
