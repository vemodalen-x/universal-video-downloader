# coding.spec — Baseline code rules (advisory) + artifact provenance

> Advisory layer: the model owns this judgment. Kept deliberately short and project-agnostic —
> concrete toolchains/class-lists/paths are **instance-owned** (in your repo's extension, not here).

## 1. Baseline style (advisory)
- Minimal, local, reversible changes; preserve existing architecture & call chains unless the task is a refactor.
- Reuse existing naming/data-flow; meaningful names; explicit constants over magic numbers.
- Early-return on invalid state; validate pointers/sizes/ranges; fail fast, no silent fallback.
- Debug output gated by existing switches; temporary debug code is removable and recorded in the task file.
- Match existing line-endings/encoding; no broad auto-format churn in review-sensitive files.

## 2. Memory / resource lifetime (advisory, for native code)
- No `new` then pointer-overwrite in one scope; every `new`/`new[]` has a clear owner + matching `delete` on all paths.
- Prefer RAII / smart pointers / stack objects over raw `new/delete` in new code.
- For multi-return functions use one unified cleanup path or RAII guards.
- Heavy resource members (image buffers, GPU handles): clear/reset at the confirmed terminal point of their
  use chain. Concrete class names + cleanup methods are **instance-owned**, not listed here.

## 3. Cross-platform (advisory)
New/modified code compiles on all toolchains the project declares. Toolchain-specific APIs only behind
explicit platform macros with both branches. Prohibited-API lists are instance-owned.

## 4. Artifact provenance
Shipped deployment/conversion artifacts (binaries, converter scripts, exported/quantized models, alignment
reports) are recorded in the project's single provenance index pinning: content **hash**, canonical
**machine/dir**, and **what it was produced from** (source commit + input model). Reusable converters live
upstream; project-specific artifacts live in the project's own VCS. Obligation is here; concrete paths are
instance-owned.

## 5. Comments (advisory, progressive)
Function-level comments for in-scope functions when behavior changes or a function is read for analysis.
Signature `@<comment_author>-comment` (author from `vemo.config.yaml`). No line-by-line narration.

> Note: none of §1–§5 is hook-enforced — these are judgment calls. Only `safety.spec` rules are mechanical.
> Keeping advice and enforcement in separate files is the point.
