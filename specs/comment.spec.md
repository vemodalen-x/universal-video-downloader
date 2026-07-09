# comment.spec — Function-comment governance (advisory)

> Function comments are a first-class maintainability concern, but still **advisory** (judgment, not a hook):
> the judge may flag gaps on R2, but a missing comment is not a mechanical safety block. Source-code comments
> only — not docs.

## Coverage trigger (progressive, not all-at-once)
If a task changes code, comment scope = functions that are (1) the owning functions of modified code,
(2) directly involved by the changed behavior, (3) directly called where understanding matters for correctness.
**Read-trigger:** if you read a source function for analysis/debug and find missing/non-compliant comments,
complete them in the same task (progressive coverage). Don't mass-rewrite good existing comments.

## Required fields (function-level, not line-by-line)
Input interface (source/format/range/units, preconditions) · Output (format/range + side effects) · key
internal stages (critical algorithm steps only) · key params (thresholds/weights/switches) · shared-state deps.

## Signature
Agent-authored comments carry `@<comment_author>-comment` (author from `vemo.config.yaml → project.comment_author`)
so ownership is traceable. Keep factual and concise; no noise restating obvious syntax.

## Review
Mark each touched function `approved` | `pending`. `pending` means "don't fully trust this comment — re-confirm
from code." Request user review for comment changes in the task; record the decision in the task file.

## Acceptance (advisory)
For code-change tasks the judge checks that in-scope functions are commented and the task records comment status.
This does NOT block a push by itself — it is a quality signal the judge weighs, per `verify.spec`.
