---
id: T-20260910-mainline-pr-integration
risk: R1
change_class: integration
state: AcceptancePassed
scope_in: ["CHANGELOG.md", "README.md", "THIRD_PARTY_NOTICES.md", "UniversalVideoDownloader.spec", "build.ps1", "m3u8_core.py", "m3u8_desktop_app.py", "requirements-release.txt", "requirements.txt", "tests/test_desktop_app.py", "tests/test_m3u8_core.py", "tests/test_verify_release_package.py", "tools/desktop_ux_smoke.py", "tools/verify_release_package.py", "tasks/Archive/**", "tasks/T-20260910-mainline-pr-integration.md"]
scope_out: [".gitignore", "photo_archive/**", "vemo_photo/**", "eval/out/**", "enforcement/**", "specs/**", ".github/**"]
trifecta: ["untrusted_content", "external_comms"]
acceptance: {status: passed, build_exit: 0, smoke_exit: 0, evidence: ".vemo\\run\\T-20260910-mainline-pr-integration-20260910-220404.log"}
judge: {required: false, verdict: null}
approved_commands: []
owning_chat: codex-20260910-mainline-pr-integration
heartbeat: 2026-09-10T22:03
---

# Downloader PR mainline integration

## Goal
Publish the reviewed downloader feature series as one CI-verifiable R1 integration, retaining prior accepted task records as archives and merging only after PR checks pass.

## Scope (In / Out)
- In: existing reviewed product source, tests, documentation and package metadata; archival of the historical accepted task records; one fresh integration acceptance record.
- Out: VEMO enforcement, release binaries, live provider access, user data and unrelated photo-product work.

## Plan
1. Preserve every completed historical task record under `tasks/Archive/`, removing it from the active-task set without discarding its audit trail.
2. Verify the full integrated source delta from the current branch with tests, native UI smoke, lint, source privacy scanning and machine acceptance.
3. Update PR metadata, review CI and merge only when the remote check is green.

## Pass/Fail Criteria
- [x] Correctness: 291 integrated core/desktop/provider/recovery tests pass without regressions.
- [x] UX: native synthetic UI smoke passes at 1220x840 and 1040x720, covering search/order, progress/motion, queue controls, recovery and log scrolling.
- [x] Privacy/package: ruff, source privacy scan (`tracked=112`) and package metadata tests pass; no binary, screenshot or user data enters the commit.
- [x] Delivery: one active R1 integration task has a current machine receipt; GitHub CI run `34487018516` passed and PR #11 merged as `02d426a5e9342008d121d3563014cba205fe19f8`.

## Execution Log
- 2026-09-10 PR #11 exposed a deterministic CI failure: its 10 historical task files each required a distinct receipt, while CI produced one receipt. This integration keeps the historical content but archives the completed records so the full source delta is certified by one fresh integration receipt.
- 2026-09-10 Local verification: `python -m pytest tests -q` passed (291 passed); targeted `ruff check` passed; source privacy scan passed (`tracked=112`); `tools/desktop_ux_smoke.py` passed at both supported window sizes; machine acceptance passed with build exit 0 and smoke exit 0 using the evidence log above.
- 2026-09-10 Remote verification: PR #11 `vemo` check passed in GitHub Actions run `34487018516`; PR #11 merged into `main` as `02d426a5e9342008d121d3563014cba205fe19f8`.

## Read Audit
- Reviewed PR #11, the failed GitHub Actions log, the VEMO pre-push task selection logic, and the complete source-only diff to `main`.

## Conclusion
Outcome: accepted | Decision: merge | Key Evidence: machine receipt `.vemo/run/T-20260910-mainline-pr-integration-20260910-220404.log`, 291 tests, native UX smoke, privacy/package scan, GitHub Actions run `34487018516`, and merged PR #11 (`02d426a5e9342008d121d3563014cba205fe19f8`) | Risk: no live provider or binary-release claim; existing behavior remains covered by source and native synthetic tests | Next Action: archive this completed task record during routine governance maintenance.
