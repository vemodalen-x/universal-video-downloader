#!/usr/bin/env python3
"""skill_check — a transparent quality bar + consistency audit for VEMO's own skills.

VEMO ships skills under `skill/<name>/SKILL.md` but, before this, `selfcheck` only asserted a
SKILL.md *exists*. This adds the missing layer: a scored quality check and a catalog<->disk
consistency audit. It is deliberately SELF-CONTAINED (does not touch the verdict engine
`task_state.py`) and wired in additively via `bin/vemo` + the eval harness.

Design:
- **Gating vs advisory.** Only structural, drift-catching rules gate (frontmatter present,
  name==dir, description shape, catalog<->disk parity, cited scripts resolve, no duplicate names).
  Description *quality* signals (a when-to-use cue) are ADVISORY — surfaced, never failing a
  well-formed skill. This keeps the bar honest without punishing VEMO's existing skills.
- **VEMO naming, not VEMO_SKILLS naming.** VEMO skills are noun/verb-noun (`call-graph`,
  `docs-sync`) — there is NO gerund rule here (that is the sibling skill-home's local rule).
- Pure stdlib; resolves its own root; fail-closed (error prints + non-zero exit).

The consistency audit is the entropy-reduction idea (no orphan catalog rows, no unlisted skill,
no dangling backing script) applied to the skill registry — mechanized where it is syntactic.
"""
import argparse
import os
import re
import sys
import tempfile

ROOT = os.environ.get("VEMO_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

USE_CUES = ("use when", "use it to", "use this", "when ", "auto", "用于", "当 ")


def _frontmatter(path):
    """Minimal YAML-ish frontmatter reader (name/description are single-line in VEMO skills)."""
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {}
    s = txt.lstrip()
    if not s.startswith("---"):
        return {}
    end = s.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in s[3:end].splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip("'\"")
    return fm


def _skill_dirs(root):
    base = os.path.join(root, "skill")
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base)
                  if os.path.isdir(os.path.join(base, d)) and not d.startswith("_")
                  and os.path.exists(os.path.join(base, d, "SKILL.md")))


def _catalog_names(root):
    """Skill names registered in skill/_catalog.md — the leading **bold** token of each table row."""
    cat = os.path.join(root, "skill", "_catalog.md")
    if not os.path.exists(cat):
        return None
    txt = open(cat, encoding="utf-8", errors="replace").read()
    return set(re.findall(r"(?m)^\|\s*\*\*([a-z0-9][a-z0-9-]*)\*\*", txt))


def _cited_scripts(skill_dir):
    """Backing scripts referenced by a SKILL.md, as relative paths (e.g. scripts/cg.py)."""
    txt = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8", errors="replace").read()
    return set(re.findall(r"`?(scripts/[A-Za-z0-9_./-]+\.py)`?", txt))


def collect(root):
    """Gather per-skill facts once; both score and audit build on this."""
    rows = []
    for name in _skill_dirs(root):
        d = os.path.join(root, "skill", name)
        fm = _frontmatter(os.path.join(d, "SKILL.md"))
        dangling = sorted(s for s in _cited_scripts(d) if not os.path.exists(os.path.join(d, s)))
        rows.append({
            "name_dir": name, "dir": d,
            "name": fm.get("name", ""), "description": fm.get("description", ""),
            "dangling_scripts": dangling,
        })
    return rows


# ── quality score ─────────────────────────────────────────────────────────────
def score_skills(root):
    rows = collect(root)
    cat = _catalog_names(root)
    disk = {r["name_dir"] for r in rows}
    dims = []

    def dim(name, ok, gating, evidence):
        dims.append({"dim": name, "pass": bool(ok), "gating": gating, "evidence": evidence})

    fm_ok = all(r["name"] and r["description"] for r in rows)
    dim("frontmatter (name+description present)", fm_ok, True,
        "all present" if fm_ok else "; ".join(f"{r['name_dir']}: missing" for r in rows if not (r["name"] and r["description"]))[:200])

    nd = [r["name_dir"] for r in rows if r["name"] and r["name"] != r["name_dir"]]
    dim("name == directory", not nd, True, "ok" if not nd else "mismatch: " + ", ".join(nd))

    bad_desc = [r["name_dir"] for r in rows if not r["description"] or "<" in r["description"] or ">" in r["description"]]
    dim("description well-formed (non-empty, no angle brackets)", not bad_desc, True,
        "ok" if not bad_desc else "issues: " + ", ".join(bad_desc))

    if cat is None:
        dim("catalog<->disk parity", False, True, "skill/_catalog.md missing")
    else:
        only_disk, only_cat = sorted(disk - cat), sorted(cat - disk)
        dim("catalog<->disk parity", not only_disk and not only_cat, True,
            "clean (%d skills)" % len(disk) if not only_disk and not only_cat
            else "unlisted=%s orphan_rows=%s" % (only_disk, only_cat))

    dangling = {r["name_dir"]: r["dangling_scripts"] for r in rows if r["dangling_scripts"]}
    dim("cited backing scripts resolve", not dangling, True,
        "ok" if not dangling else "dangling: " + str(dangling)[:200])

    names = [r["name_dir"] for r in rows]
    dups = sorted({n for n in names if names.count(n) > 1})
    dim("no duplicate skill names", not dups, True, "ok" if not dups else "dups: " + ", ".join(dups))

    # ADVISORY (surfaced, never gating): a when-to-use cue in the description aids auto-invocation.
    no_cue = [r["name_dir"] for r in rows if not any(c in r["description"].lower() for c in USE_CUES)]
    dim("description has a when-to-use cue (advisory)", not no_cue, False,
        "all have a cue" if not no_cue else "advisory — no explicit cue: " + ", ".join(no_cue))

    gating = [d for d in dims if d["gating"]]
    passed = sum(1 for d in gating if d["pass"])
    ok = passed == len(gating)
    return {"skills": len(rows), "gating_passed": passed, "gating_total": len(gating),
            "score": round(passed / len(gating), 3) if gating else 0.0, "pass": ok, "dims": dims}


# ── consistency audit ──────────────────────────────────────────────────────────
def audit_consistency(root):
    rows = collect(root)
    cat = _catalog_names(root)
    disk = {r["name_dir"] for r in rows}
    issues = []
    if cat is None:
        issues.append("skill/_catalog.md missing (no registry to audit against)")
    else:
        for n in sorted(disk - cat):
            issues.append("skill '%s' on disk but NOT registered in _catalog.md (unlisted skill)" % n)
        for n in sorted(cat - disk):
            issues.append("_catalog.md lists '%s' with no skill/%s/ on disk (orphan catalog row)" % (n, n))
    for r in rows:
        if r["name"] and r["name"] != r["name_dir"]:
            issues.append("skill/%s: frontmatter name '%s' != directory (mis-placed)" % (r["name_dir"], r["name"]))
        for s in r["dangling_scripts"]:
            issues.append("skill/%s: cites `%s` but the file does not exist (dangling backing script)" % (r["name_dir"], s))
        if not r["description"]:
            issues.append("skill/%s: empty description (invisible to auto-invocation)" % r["name_dir"])
    return {"skills": len(rows), "issues": issues, "pass": not issues}


# ── roster (agent/human visibility of what skills exist) ─────────────────────────
def roster(root):
    """Compact 'what skills exist' listing (name + short purpose) for `vemo skill-roster` and the
    session brief. Reuses collect() so it never drifts from the on-disk skills."""
    out = []
    for r in collect(root):
        desc = (r["description"] or "").strip()
        short = re.split(r"(?<=[.。])\s", desc)[0] if desc else ""
        out.append((r["name_dir"], short[:96]))
    return out


# ── hermetic selftest (no model / network) ──────────────────────────────────────
def _mk(root, name, fm_name=None, desc="Do a thing. Use when a thing must be done.", script=None, cite=None):
    d = os.path.join(root, "skill", name)
    os.makedirs(d, exist_ok=True)
    body = "---\nname: %s\ndescription: %s\n---\n\n# %s\n" % (fm_name or name, desc, name)
    if cite:
        body += "\nRun `%s`.\n" % cite
    open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8").write(body)
    if script:
        os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
        open(os.path.join(d, "scripts", script), "w", encoding="utf-8").write("# x\n")


def selftest():
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    with tempfile.TemporaryDirectory() as td:
        _mk(td, "call-graph", script="cg.py", cite="scripts/cg.py")
        _mk(td, "docs-sync")
        open(os.path.join(td, "skill", "_catalog.md"), "w", encoding="utf-8").write(
            "# cat\n| Skill | x |\n|---|---|\n| **call-graph** | a |\n| **docs-sync** | b |\n")
        rep = score_skills(td)
        aud = audit_consistency(td)
        check("clean home scores pass", rep["pass"] and rep["gating_passed"] == rep["gating_total"])
        check("clean home audits clean", aud["pass"])
        check("noun names accepted (no gerund rule)", rep["pass"])

        # unlisted skill on disk -> parity + audit catch it
        _mk(td, "rogue-skill")
        rep2 = score_skills(td)
        aud2 = audit_consistency(td)
        check("unlisted skill fails parity", not rep2["pass"])
        check("audit flags the unlisted skill", any("rogue-skill" in i for i in aud2["issues"]))

    with tempfile.TemporaryDirectory() as td:
        _mk(td, "good-skill")
        open(os.path.join(td, "skill", "_catalog.md"), "w", encoding="utf-8").write(
            "| **good-skill** | a |\n| **ghost-skill** | b |\n")
        aud = audit_consistency(td)
        check("audit flags orphan catalog row", any("ghost-skill" in i and "orphan" in i for i in aud["issues"]))

    with tempfile.TemporaryDirectory() as td:
        _mk(td, "bad-name", fm_name="different-name")
        open(os.path.join(td, "skill", "_catalog.md"), "w", encoding="utf-8").write("| **bad-name** | a |\n")
        rep = score_skills(td)
        check("name != dir fails gating", not rep["pass"])

    with tempfile.TemporaryDirectory() as td:
        _mk(td, "dangles", cite="scripts/missing.py")
        open(os.path.join(td, "skill", "_catalog.md"), "w", encoding="utf-8").write("| **dangles** | a |\n")
        aud = audit_consistency(td)
        check("dangling backing script flagged", any("dangling" in i for i in aud["issues"]))

    ok_all = all(ok for _, ok in results)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("[skill_check selftest] %d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
    return 0 if ok_all else 1


def _print_score(rep):
    print("VEMO skill-score: %s  %d/%d gating dims  (%d skills, score=%.2f)" % (
        "PASS" if rep["pass"] else "FAIL", rep["gating_passed"], rep["gating_total"], rep["skills"], rep["score"]))
    for d in rep["dims"]:
        tag = "PASS" if d["pass"] else ("GAP" if d["gating"] else "info")
        print("  [%s] %s — %s" % (tag, d["dim"], d["evidence"]))


def _print_audit(rep):
    if rep["pass"]:
        print("VEMO skill-audit: OK — %d skills, catalog<->disk consistent, no dangling references" % rep["skills"])
    else:
        print("VEMO skill-audit: %d issue(s)\n  - %s" % (len(rep["issues"]), "\n  - ".join(rep["issues"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("score", "audit", "roster"):
        sp = sub.add_parser(c)
        sp.add_argument("--root", default=ROOT)
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "score":
        rep = score_skills(a.root)
        _print_score(rep)
        return 0 if rep["pass"] else 1
    if a.cmd == "audit":
        rep = audit_consistency(a.root)
        _print_audit(rep)
        return 0 if rep["pass"] else 1
    if a.cmd == "roster":
        rows = roster(a.root)
        print("VEMO skills (%d):" % len(rows))
        for name, short in rows:
            print("  %-30s %s" % (name, short))
        return 0
    if a.cmd == "selftest":
        return selftest()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # fail closed
        print("error:%s:%s" % (type(e).__name__, e))
        sys.exit(3)
