---
id: T-20260709-vemo-framework-integration
risk: R2
state: AcceptancePassed
scope_in: [".gitignore", ".claude/settings.json", ".github/**", "AGENTS.md", "agents/**", "bin/**", "docs/**", "enforcement/**", "presets/**", "skill/**", "specs/**", "tasks/**", "vemo.config.yaml", "vemo.config.preset.yaml", ".vemo/judge.jsonl"]
scope_out: ["requirements.txt", "README_photo_archive.md", "photo_archive_*.py", "tests/test_photo_archive_core.py", "vemo_photo/**"]
trifecta: []
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260709-vemo-framework-integration-20260709-214453.log"
judge:
  required: true
  verdict: pass
approved_commands: []
owning_chat: "codex-20260709-vemo-framework"
heartbeat: "2026-07-09T21:44"
---

# VEMO framework integration

## Goal
Integrate VEMO and VEMO_SKILLS into the local PC project and keep the framework updates synchronized with the source repositories.

## Scope (In / Out)
- In: VEMO governance files, VEMO skill roster, Codex/Claude skill binding, CI hook wiring, and framework documentation.
- Out: photo archive application changes and dependency changes already present in the worktree.

## Pass/Fail Criteria
- [Correctness] WHEN the project loads VEMO, `python bin\vemo selfcheck` SHALL exit 0.
- [Skills] WHEN the VEMO skill catalog is checked, `python bin\vemo-skills selfcheck` in VEMO_SKILLS SHALL exit 0.
- [Build] `python -m compileall -q m3u8_core.py m3u8_desktop_app.py photo_archive_core.py photo_archive_cli.py photo_archive_app.py tests` SHALL exit 0.
- [Smoke] `python -m pytest tests -q` SHALL exit 0.
- [Governance] `python bin\vemo verify` SHALL write a passing receipt.

## Plan
- Import VEMO governance files and configure local Windows-compatible commands.
- Bind VEMO_SKILLS into the project skill surface.
- Generalize playbook patterns and diagnostic prompting into VEMO docs and VEMO_SKILLS.
- Run selfcheck, smoke tests, and VEMO verification before commit.

## Execution Log
- 2026-07-09 Cloned local VEMO and VEMO_SKILLS source repositories.
- 2026-07-09 Added framework docs, skill bindings, CI wiring, and Windows Python compatibility fixes.
- 2026-07-09 Added diagnostic prompting guidance based on Human 3.0 and Mr. Ranedeer source patterns.
- 2026-07-09 Verification passed with receipt `.vemo/run/T-20260709-vemo-framework-integration-20260709-214453.log`.

## Acceptance Result
- PASS: `python bin\vemo selfcheck`.
- PASS: `python bin\vemo-skills selfcheck` in VEMO_SKILLS.
- PASS: `python -m pytest tests -q` for the scoped root test set.
- PASS: `python bin\vemo verify`.

## Conclusion
Outcome: accepted. Decision: continue. Key Evidence: `.vemo/run/T-20260709-vemo-framework-integration-20260709-214453.log`. Risk: med. Next Action: commit and push scoped framework changes.
