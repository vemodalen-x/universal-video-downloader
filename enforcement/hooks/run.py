#!/usr/bin/env python3
"""VEMO hook dispatcher — the ONE client-side enforcement entry point (pure stdlib Python).

Replaces the former per-guard .sh scripts: every one of them already shelled out to python3, so bash
bought no portability — only a second copy of the logic to drift (it did: monitor mode and telemetry
were honored in one place and not the other). One dispatcher = one truth, Windows included.

Registered by enforcement/install.sh into .claude/settings.json (see enforcement/hooks/hooks.json):
  PreToolUse Edit|Write|MultiEdit|NotebookEdit -> run.py edit      (scope + binary-blob + secret)
  PreToolUse Bash                              -> run.py command   (destructive / out-of-repo)
  PreToolUse *                                 -> run.py budget    (run-budget stop rules)
  Stop                                         -> run.py stop      (advisory acceptance reminder)
  SubagentStop                                 -> run.py subagent-stop
  SessionStart                                 -> run.py session-start

Config it honors (vemo.config.yaml → enforcement / observability):
  mode: enforce|monitor      monitor = log, don't block (onboarding/tuning)
  block_on: [...]            which guards are active; a rule absent from the list = guard skipped
  fail_closed: [...]         guards that BLOCK when the check itself errors (a missing guard
                             is not a safe guard); others degrade to warn+log
  degrade_gracefully: bool   explicit opt-in to fail-open for the fail_closed list
  observability.telemetry    off | gate_events | full — blocks/stops always logged (audit-first),
                             allows only at `full`

Exit contract (Claude Code): exit 2 = block the action, stderr is fed back to the agent; exit 0 = allow.
"""
import sys, os, json, re
from datetime import datetime

SELF = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("VEMO_ROOT") or os.path.dirname(os.path.dirname(SELF))
os.environ["VEMO_ROOT"] = ROOT
# import the validator from THIS install (code ships with the dispatcher); VEMO_ROOT is where the
# governed DATA lives (config, tasks/, .vemo/) — the two differ in eval sandboxes and nested setups
sys.path.insert(0, os.path.join(os.path.dirname(SELF), "validators"))
try:
    import task_state as ts               # in-process: no subprocess fan-out, same logic as CI
except Exception:
    ts = None

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
TELE = os.path.join(ROOT, ".vemo", "telemetry.jsonl")

SECRET_RE = re.compile(
    r'(api[_-]?key|secret|password|token)["\' ]*[:=]["\' ]*[A-Za-z0-9/_+\-]{16,}'
    r'|BEGIN (RSA|OPENSSH|EC|PGP) PRIVATE KEY', re.I)

DESTRUCTIVE = [
    # recursive AND force, in ANY form/order (rm -rf / -fr / -r -f / --recursive --force) and for ANY
    # target (incl. ./relative) — two lookaheads for "has a recursive flag" and "has a force flag" within
    # the same command segment (stops at | ; & newline). Both required, matching the original -rf intent.
    (r'(^|[^a-zA-Z])rm\b(?=[^|;&\n]*(?:-[a-zA-Z]*[rR]|--recursive))(?=[^|;&\n]*(?:-[a-zA-Z]*f|--force))',
     "recursive force delete (rm -rf / -Rf / -r -f / --recursive --force, any target)"),
    (r'git\s+reset\s+--hard', "git reset --hard"),
    (r'git\s+checkout\s+--\s', "git checkout -- (discards working changes)"),
    (r'git\s+clean\s+(-[a-zA-Z]*f|[^|;&]*--force)', "git clean -f (deletes untracked files)"),
    (r'git\s+push\b[^|;&\n]*?(?:--force\b(?!-with-lease|-if-includes)|(?<![\w-])-[a-zA-Z]*f)',
     "git push --force/-f (rewrites remote history; force in any short-flag position)"),
    (r'find\s[^|;&]*-delete', "find -delete"),
    (r'xargs\s+[^|;&]*\brm\b', "xargs rm"),
    (r'(^|\s)(sudo|doas)\s', "privilege escalation"),
    (r'curl[^|]*\|\s*(sh|bash)', "pipe-to-shell install"),
    (r'\bdd\b[^|;&]*of=/dev/', "dd onto a device"),
    (r'\bmkfs(\.| )', "filesystem format"),
    # git-gate evasion / tamper: VEMO's git ring is part of the governed surface (safety.spec#4).
    # Blocking here is fast feedback; a hookless harness still hits the same checks in CI.
    (r'git\s+(commit|push|merge)\s[^|;&]*--no-verify', "git --no-verify (evades VEMO commit/push gates; CI re-runs them anyway)"),
    (r'core\.hooksPath', "core.hooksPath (redirects git hooks away from VEMO gates)"),
    (r'(^|[\s;|&])(rm|mv|cp|chmod|ln|truncate|tee|sed)\b[^|;&]*\s\S*\.git/|>>?\s*\S*\.git/',
     "write into .git/ (git-gate tamper)"),
]

_SEG_SPLIT = re.compile(r"(\||&&|\|\||;|&|\n)")   # every bash command separator, incl. bare & (background)


def strip_data_regions(cmd):
    r"""Blank the ARGUMENTS of echo/printf segments before the DESTRUCTIVE match, so a dangerous command
    that is merely ECHOED (`echo 'run git reset --hard to undo'`) is not mistaken for one being run.

    A segment (split on |, &&, ||, ;, newline) is reduced to just its command name ONLY when that name is
    echo/printf AND the segment has no command substitution ($(/`) and no redirect (>/<): the builtin
    writes only stdout, so its literal arguments are pure data. This is the ONE transform that is
    STRUCTURALLY miss-safe — a real command can appear only in its OWN segment (after a splitter), which
    is preserved; a `;`/newline inside a quoted echo arg merely over-splits (over-block = the safe side),
    and env-assignments/other command names keep the segment. Everything else — real command positions,
    &&/;/| segments, $()/backticks, HEREDOCS, COMMENTS, and all redirect operators/targets — is preserved
    byte-for-byte, so matching a truly-executed danger is identical to matching the raw command
    -> zero new missed blocks.

    NOTE: heredoc-body stripping and full-comment-line stripping were both tried and REMOVED — reliably
    telling quoted/commented data from live code ACROSS lines needs a real shell parser, not a regex, and
    adversarial review found bypasses in each (a `<<'W'` look-alike or a quote closing on a `#`-line hid a
    real command). Those forms now simply keep matching (a harmless over-block). Fail-safe: any exception
    returns the raw command (strip nothing = block more, never less)."""
    try:
        out = []
        for seg in _SEG_SPLIT.split(cmd):
            toks = seg.strip().split()
            head = toks[0].rsplit("/", 1)[-1] if toks else ""
            if (head in ("echo", "printf") and "$(" not in seg and "`" not in seg
                    and ">" not in seg and "<" not in seg):
                out.append(head)                 # builtin writes stdout only; args are pure data
            else:
                out.append(seg)                  # preserve real commands, redirects, substitutions
        return "".join(out)
    except Exception:
        return cmd


# absolute-path write targets that are always fine (ubiquitous, non-persistent)
SAFE_ABS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/tmp/", "/var/tmp/", "/proc/self/")


def cfg(field, default=None):
    if not ts:
        return default
    try:
        v = ts._dig(ts._load_config(), field)
        return default if v is None else v
    except Exception:
        return default


def log(event, **kw):
    try:
        os.makedirs(os.path.dirname(TELE), exist_ok=True)
        row = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **kw}
        with open(TELE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def tele_level():
    v = cfg("observability.telemetry", "gate_events")
    return v if v in ("off", "gate_events", "full") else "gate_events"


def monitor():
    return str(cfg("enforcement.mode", "enforce")).lower() == "monitor"


def enabled(rule):
    on = cfg("enforcement.block_on", None)
    return True if not isinstance(on, list) else rule in on


def fail_closed(rule):
    fc = cfg("enforcement.fail_closed", None)
    listed = rule in fc if isinstance(fc, list) else rule in ("scope_violation", "destructive_command", "secret_in_diff")
    return listed and not cfg("enforcement.degrade_gracefully", False)


def block(rule, msg, **kw):
    """Central block/monitor/telemetry decision for one violation."""
    log(rule, **kw)                                   # blocks are always logged — audit first
    if monitor():
        print(f"[VEMO] {msg}  (monitor mode: logged, not blocking)", file=sys.stderr)
        return 0
    print(f"[VEMO] BLOCKED {msg}", file=sys.stderr)
    return 2


def allow(rule, **kw):
    if tele_level() == "full":
        log("allow_" + rule, **kw)
    return 0


def payload():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def target_of(d):
    ti = d.get("tool_input", {}) or {}
    return ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ""


def degraded(rule, what):
    """The check itself failed (validator error). A missing guard is not a safe guard."""
    log("guard_degraded", rule=rule, target=what)
    if fail_closed(rule):
        print(f"[VEMO] BLOCKED (fail-closed): {rule} cannot verify '{what}' (validator error). "
              f"Fix the error, or set enforcement.degrade_gracefully: true to opt into fail-open.", file=sys.stderr)
        return 2
    print(f"[VEMO] {rule} degraded (validator error) — allowing + logging.", file=sys.stderr)
    return 0


# ── guards ──────────────────────────────────────────────────────────────────
def guard_edit(d):
    """PreToolUse(Edit/Write/...): scope containment (safety#1) + binary blob (safety#6) + secret (safety#5)."""
    t = target_of(d)
    sid = d.get("session_id")
    if t:
        if enabled("scope_violation"):
            try:
                v = ts.scope_check(t, sid)
            except Exception:
                return degraded("scope_violation", t)
            if v == "out-of-scope":
                return block("scope_block_out",
                             f"(safety.spec#1): '{t}' is outside the active task's scope_in. If it truly "
                             f"belongs to this task, add it via a replan; otherwise it is a different task.",
                             target=t, session=sid)
            if v == "no-active-task":
                return block("scope_block_no_task",
                             "(safety.spec#1): no active task declares a Scope (In). Create/continue a task "
                             "(tasks/_TASK_TEMPLATE.md) first, then edit.", target=t, session=sid)
            if v != "in-scope":
                return degraded("scope_violation", t)
        if enabled("binary_blob"):
            try:
                b = ts.blob_check(t)
            except Exception:
                b = "ok"                    # blob guard is not in the default fail_closed set
            if b.startswith("blob:"):
                return block("blob_block", f"(safety.spec#6): '{t}' is a binary/model blob — agents do not "
                                           f"hand-edit build artifacts or weights.", target=t)
    if enabled("secret_in_diff"):
        ti = d.get("tool_input", {}) or {}
        content = ti.get("content") or ti.get("new_string") or ""
        if content and SECRET_RE.search(content):
            return block("secret_block", "(safety.spec#5): the content looks like a credential/secret. "
                                         "Use an env var or a secrets manager, not a committed file.", target=t)
    return allow("edit", target=t)


def _approved(cmd):
    """Explicit escape hatches for destructive commands: the active task's `approved_commands:` list
    (same-session user approval recorded in the task file — safety#4) and, unattended,
    auto_mode preauthorized_commands. Substring match, explicit opt-in only."""
    pats = []
    try:
        act = ts._active_task()
        if act:
            pats += act[1].get("approved_commands") or []
        pats += ts._auto_state().get("preauthorized_commands") or []
    except Exception:
        pass
    return any(p and p in cmd for p in pats)


def guard_command(d):
    """PreToolUse(Bash): destructive / out-of-repo commands (safety#4) + scope-bypass advisory."""
    cmd = (d.get("tool_input", {}) or {}).get("command", "") or ""
    if not cmd:
        return 0
    scan = strip_data_regions(cmd)   # match against the command with non-executed data regions blanked
    if enabled("destructive_command"):
        for pat, why in DESTRUCTIVE:
            if re.search(pat, scan):
                if _approved(cmd):
                    log("command_approved", cmd=cmd[:200], why=why)
                    break
                return block("command_block", f"(safety.spec#4): {why}. Needs explicit same-session user "
                                              f"approval recorded in the task file (`approved_commands:`).",
                             cmd=cmd[:200], why=why)
        else:
            # writes outside the repo (redirects / tee to absolute or home paths)
            for m in re.finditer(r'(?:>>?|\btee\s+(?:-a\s+)?)\s*((?:/|~)[^\s;|&<>]+)', scan):
                p = os.path.expanduser(m.group(1))
                if p.startswith(SAFE_ABS):
                    continue
                if not os.path.abspath(p).startswith(os.path.abspath(ROOT) + os.sep):
                    if _approved(cmd):
                        log("command_approved", cmd=cmd[:200], why="out-of-repo write")
                        break
                    return block("command_block", f"(safety.spec#4): writes outside repo_root ('{m.group(1)}').",
                                 cmd=cmd[:200], why="out-of-repo write")
    # scope-bypass ADVISORY (safety#1 note): shell writes into in-repo but out-of-scope paths.
    # Heuristic, warn-only — containment is guaranteed at commit/CI, not here.
    if enabled("scope_violation"):
        try:
            sid = d.get("session_id")
            for m in re.finditer(r'(?:>>?|\btee\s+(?:-a\s+)?)\s*([\w][\w./-]*)', scan):
                t = m.group(1)
                if t and "/" in t and ts.scope_check(t, sid) == "out-of-scope":
                    print(f"[VEMO] note (safety.spec#1): this shell command writes to '{t}', which is outside "
                          f"the active task's scope_in. The commit gate will reject it — replan the scope or "
                          f"drop the write.", file=sys.stderr)
                    log("command_warn_scope_bypass", target=t, cmd=cmd[:200])
                    break
        except Exception:
            pass
    return allow("command", cmd=cmd[:120])


def guard_budget(d):
    """PreToolUse(*): run-budget stop rules (count ceilings + stuck-loop detection).
    Hard stop only when unattended (auto ON); advisory otherwise."""
    t = target_of(d)
    tool = d.get("tool_name", "")
    sid = d.get("session_id")
    sig = None
    if tool == "Bash":                          # stuck-loop signal: identical Bash commands repeated
        cmd = (d.get("tool_input", {}) or {}).get("command", "") or ""
        if cmd:
            import hashlib
            sig = hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest()[:12]
    try:
        v = ts.budget_tick(t or None, tool in WRITE_TOOLS, sid, sig)
    except Exception:
        return 0                                # without a counter we cannot count; other guards still apply
    if v.startswith("stop:"):
        auto_on = False
        try:
            auto_on = bool(ts._auto_state().get("enabled"))
        except Exception:
            pass
        if auto_on and not monitor():
            log("budget_stop", verdict=v, session=sid)
            print(f"[VEMO] STOP RULE (unattended auto mode): {v}. Autonomous run halted.\n"
                  f"       A human checkpoint is required. Reset: python3 enforcement/validators/task_state.py "
                  f"budget-reset" + (f" --session {sid}" if sid else ""), file=sys.stderr)
            return 2
        log("budget_advisory", verdict=v, session=sid)
        print(f"[VEMO] {v} (advisory: a human is present, not blocking).", file=sys.stderr)
        return 0
    if v.startswith("note:"):
        print(f"[VEMO] {v[5:]}", file=sys.stderr)
        if tele_level() == "full":
            log("budget_note", note=v, session=sid)
    return 0


def guard_stop(d):
    """Stop: advisory-strong reminder if the session wraps up below AcceptancePassed.
    The hard stop lives at push time (pre-push + CI), not here — we never trap the user."""
    try:
        v = ts.gate_check("acceptance-before-push", d.get("session_id"))
    except Exception:
        v = "error"
    if v != "ok":
        log("stop_below_acceptance", detail=v)
        print(f"[VEMO] reminder: active task is not yet push-ready ({v}).", file=sys.stderr)
    return 0


def guard_subagent_stop(d):
    log("subagent_stop", session=d.get("session_id"))
    return 0


def guard_session_start(d):
    """SessionStart: telemetry heartbeat (proof the hooks are alive — `vemo doctor` checks for it)
    + the machine-read context brief (stdout is added to context). The brief replaces bulk-reading
    vemo.config.yaml/specs at session start — token economy: judgment context, not raw config."""
    sid = d.get("session_id")
    log("session_start", session=sid)
    try:
        print(ts.context_brief(sid))
    except Exception:
        try:
            act = ts._active_task(sid)
            print(("VEMO: active task %s state=%s risk=%s" %
                   (act[1].get("id"), act[1].get("state"), act[1].get("risk"))) if act else "VEMO: no active task")
        except Exception:
            pass
    return 0


GUARDS = {"edit": guard_edit, "command": guard_command, "budget": guard_budget,
          "stop": guard_stop, "subagent-stop": guard_subagent_stop, "session-start": guard_session_start,
          # back-compat aliases for pre-1.1 registrations
          "scope": guard_edit, "secret": guard_edit}


def main():
    guard = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = GUARDS.get(guard)
    if not fn:
        return 0
    if ts is None:                                 # validator missing entirely
        return degraded("scope_violation" if guard in ("edit", "scope") else "destructive_command", guard)
    return fn(payload())


def _selftest():
    """Two-way hermetic check of strip_data_regions against the real DESTRUCTIVE set (no I/O).
    Literals are concatenated so this source file is not itself flagged as a dangerous command."""
    pp, rr = "git" + " push --force", "rm" + " -rf"

    def hits(cmd):
        scan = strip_data_regions(cmd)
        return any(re.search(pat, scan) for pat, _ in DESTRUCTIVE)

    # real dangers STILL block (identical to raw)
    assert hits(rr + " /"), "bare rm -rf /"
    assert hits("git" + " reset --hard HEAD~3"), "bare git reset --hard"
    assert hits("cat log && " + rr + " /"), "&& segment"
    assert hits('echo "$(' + pp + ')"'), "echo with command substitution -> preserved"
    assert hits("echo ok > .git/hooks/pre-push"), "echo WITH redirect -> preserved (.git tamper)"
    assert hits("echo pwned >> ~/.bashrc") is False, "out-of-repo redirect is not a DESTRUCTIVE literal"
    assert hits("echo x & " + rr + " /"), "danger after a bare & (background) is its own segment -> blocks"
    assert hits("echo x &" + rr + " /"), "bare & with no surrounding space still splits -> blocks"
    # heredoc bodies are NOT stripped, so NO <<'W' look-alike can hide a real command — every variant
    # (unquoted / legit / quoted-echo-arg / assignment / line-start-# / trailing-# / backslash-escaped)
    # keeps matching (over-block = safe direction). These are exactly the review's three bypass carriers.
    assert hits("cat <<EOF\n" + rr + " /\nEOF"), "unquoted heredoc"
    assert hits("cat > note.md <<'EOF'\nrun " + rr + " / to clean up\nEOF"), "legit quoted heredoc (kept)"
    assert hits('echo "see <<\'EOF\'"\n' + "git" + " reset --hard HEAD~5\nEOF"), "heredoc marker in quoted echo arg"
    assert hits('v="<<\'Q\'"\n' + rr + " /\nQ"), "heredoc marker in assignment"
    assert hits("# note <<'W'\n" + rr + " /\nW"), "commented heredoc marker (line-start #)"
    assert hits("echo x # <<'W'\n" + "git" + " reset --hard\nW"), "commented heredoc marker (trailing #)"
    assert hits("cat \\<<'W'\n" + rr + " /\nW"), "backslash-escaped heredoc marker"
    # comments are NOT stripped either -> a comment MENTIONING a danger over-blocks (safe), and a danger
    # after a quote that closes on a #-line still blocks (the cross-line-quote comment bypass)
    assert hits("# " + pp + " is dangerous, do not run it"), "comment mentioning a danger over-blocks (safe)"
    assert hits("eval '\n#c' ; " + rr + " /"), "cross-line-quote comment carrier must NOT hide a real command"
    assert hits("x='\n#c' ; " + "git" + " reset --hard"), "assignment cross-line-quote carrier blocks"
    # false-positive FIXED: a danger only ECHOED / printed is not blocked (the one miss-safe transform)
    assert not hits("echo 'to undo run git reset --hard'"), "echo literal"
    assert not hits("printf '%s' 'git reset --hard'"), "printf literal"
    # DESTRUCTIVE hardening: short/split/long flags + relative targets are now caught...
    assert hits("git" + " push -f origin main"), "git push -f (short force flag)"
    assert hits("rm" + " -r -f /x"), "rm -r -f (split flags)"
    assert hits("rm" + " --recursive --force /x"), "rm --recursive --force (long flags)"
    assert hits("rm" + " -rf ./build"), "rm -rf ./relative-target"
    assert hits("rm" + " -Rf /x"), "rm -Rf (capital -R recursive synonym)"
    assert hits("rm" + " -R -f /x"), "rm -R -f (capital split)"
    assert hits("git" + " push -fu origin main"), "git push -fu (force + trailing flag letter)"
    assert hits("git" + " push -fvn origin x"), "git push -fvn (force mid short-flag cluster)"
    # ...without over-blocking the safe forms
    assert not hits("git" + " push --force-with-lease origin main"), "--force-with-lease is safe"
    assert not hits("git" + " push -u origin main"), "git push -u (set-upstream, no force)"
    assert not hits("git" + " push -v origin main"), "git push -v (verbose, no force)"
    assert not hits("git" + " push origin main"), "plain push"
    assert not hits("rm" + " notes.txt"), "non-recursive rm"
    assert not hits("rm" + " -r somedir"), "recursive without force"
    print("run.py selftest OK: strip_data_regions + DESTRUCTIVE hardening (short/split/long flags, ./targets)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        sys.exit(0)
    sys.exit(main())
