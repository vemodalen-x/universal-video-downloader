---
name: docs-sync
description: Keep README / GUIDE / docs in sync with the actual framework state (version, CLI verbs, skill list, repo structure). Use after adding or changing a skill, bumping the version, or changing a command — or when asked to update the docs.
---

# Docs Sync

The **single owner** of documentation updates — other skills (governance-release/-contribute) delegate doc edits
here instead of editing docs directly. This keeps README, guide, and reference updates in one route.

**When**
- auto: during `governance-release` / `governance-contribute`; when a skill, command, or version changes.
- manual: "update docs", "sync readme", "refresh the guide".

**Checklist** (skip what didn't change)
- **Version line** — from `VERSION` / `vemo.config.yaml` (mandatory on every release).
- **CLI table** — scan `bin/vemo` (and `vemo help`); add/remove/relabel verbs.
- **Skills table** — scan every `skill/*/SKILL.md` frontmatter (`name`, `description`); match count + intent.
- **Repo structure** — if top-level dirs / skills were added or removed.
- **Consistency** — README ↔ `docs/GUIDE.html` ↔ `docs/MENTAL_MODEL.md` say the same thing.

**Rules** — version/CLI/skill tables may update unattended; **present judgment-y prose** (feature descriptions,
design rationale) to the user before writing. Don't add per-model branding. Keep each doc to its one
[Diátaxis](https://diataxis.fr/) job — a tutorial that starts listing every flag is drifting into reference.
