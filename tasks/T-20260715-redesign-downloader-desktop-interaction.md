---
id: T-20260715-redesign-downloader-desktop-interaction
risk: R1
change_class: standard
state: AcceptancePassed
scope_in: ["m3u8_desktop_app.py", "tests/test_desktop_app.py", "tasks/T-20260715-redesign-downloader-desktop-interaction.md"]
scope_out: []
trifecta: []
verification:
  profile: focused
  commands:
    test: "python -m pytest -q tests/test_desktop_app.py"
    lint: "python -m ruff check m3u8_desktop_app.py tests/test_desktop_app.py"
    smoke: "python -m py_compile m3u8_desktop_app.py"
acceptance:
  status: passed
  build_exit: 0
  smoke_exit: 0
  evidence: ".vemo/run/T-20260715-redesign-downloader-desktop-interaction-20260715T130136Z.log"
judge:
  required: false
  verdict: null
approved_commands: []
owning_chat: ""
heartbeat: 2026-07-15T13:01:36Z
---

# Redesign downloader desktop interaction

## Goal
Bring the Windows downloader UI from a feature-oriented utility layout to a calm, task-oriented workspace with a clear primary action, visible recovery state, and a history workflow that supports search and reuse.

## Scope (In / Out)
- In: the downloader desktop UI, its focused tests, and this task's acceptance evidence.
- Out: everything else.

## Pass/Fail Criteria
- [x] The first viewport makes the URL input, parse action, current task state, and next recovery action visually obvious at 1040x720 and above.
- [x] Advanced options are secondary to the primary workflow and do not compete with the parse/download actions.
- [x] Candidate and history surfaces support scan-friendly status, selection, filtering, and reuse without changing downloader core behavior.
- [x] Keyboard shortcuts provide URL focus and parse actions, and empty/loading/error/completed states remain explicit.
- [x] Focused UI tests, Ruff, and Python compile smoke all pass.

## Plan
- Establish a compact visual token system and task-focused header/status strip.
- Improve the download workspace hierarchy, candidate selection feedback, and responsive action layout.
- Add searchable/filterable history interactions and keyboard shortcuts without changing core download jobs.
- Add focused pure/UI-adjacent tests and run the focused verification profile.

## Execution Log
- 2026-07-15T12:55:48Z Task created by `vemo task create`.
- 2026-07-15T12:56:38Z Design review identified three gaps: URL-first hierarchy is diluted by parallel controls, task status/recovery is not prominent, and history lacks search/filter/reuse affordances. Implementing a task-oriented UI pass without changing downloader core behavior.
- 2026-07-15T13:01:36Z Implemented the task-oriented UI pass: visible status badge, URL clear/shortcut actions, candidate empty/count states, searchable/filterable history with colored statuses, explicit no-selection recovery guidance, and focused history matching tests. Focused tests 11 passed, Ruff passed, py_compile passed, and full regression passed 134 tests.

## Acceptance Result
Passed. Focused VEMO verification ran the UI test suite, scoped Ruff, and Python compile smoke with exit code 0. Full regression also passed 134 tests.

## Conclusion
The downloader UI now presents a clearer primary workflow and a recoverable task history without changing the downloader core.
