---
name: governance-sync
description: Pull template-owned framework updates from the VEMO upstream into a consuming project. Use at session start (continuity check) or when asked to check for governance / framework updates.
---

# Governance Sync

Compare the local framework version to the upstream, show what changed, and apply **only on confirmation**.
Deterministic check: `scripts/govsync.py`.

**Prereq** — GitHub CLI (`gh auth status`). If missing → print the install hint and **stop**; do not block the session.

**When**
- auto: session start, if `vemo.upstream` is set — *notify only*, never auto-apply.
- manual: "check governance updates", "sync governance", "check framework updates".

**Steps**
```bash
python3 skill/governance-sync/scripts/govsync.py check   # local vs upstream VERSION + HEAD + what differs
```
On user OK, apply updates to **template-owned files only** (`specs/`, `enforcement/`, `skill/`), then record the
synced commit in `.vemo/governance.json`.

**Never touched by sync** — `vemo.config.yaml`, `tasks/**`, `.vemo/**` (runtime), and `AGENTS.md` (hybrid →
report the diff, never overwrite). Instance values stay yours; only reusable governance flows in.
