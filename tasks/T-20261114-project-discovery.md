---
id: T-20261114-project-discovery
risk: R0
state: ImplementationDone
scope_in: ["README.md", "tasks/T-20261114-project-discovery.md"]
scope_out: ["*.py", ".github/**", "dist/**"]
trifecta: ["untrusted_content", "external_comms"]
acceptance: {status: not_applicable}
judge: {required: false, verdict: null}
approved_commands: []
owning_chat: chat-20261114-project-discovery
heartbeat: 2026-11-14T08:13Z
---

# Make the Windows download discoverable

## Goal
Help visitors find the portable Windows build and understand which features ship in it.

## Plan
Check the published assets and tagged documentation, add a bilingual quick start, and publish the repository description, topics and announcement on GitHub.

## Pass/Fail Criteria
- The download and checksum links identify uploaded assets from the same release.
- The public quick start describes the tagged build and labels source-only features.
- The documentation passes the repository metadata and privacy checks.

## Verification
The three README links matched the public release API. The downloaded ZIP matched the published SHA-256, passed its CRC check, and contained the documented executable. All 61 release-package tests passed and the source privacy scan passed.
