#!/usr/bin/env python3
"""Executable conformance runner for VEMO's own mechanisms.

Default output is intentionally compact for agent loops: all selected checks run, but passing
checks are not printed one by one unless --verbose is requested. Full details remain in
eval/out/report.json.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAL = os.path.join(ROOT, "enforcement", "validators", "task_state.py")
HOOK = os.path.join(ROOT, "enforcement", "hooks", "run.py")


def bash_exe():
    if os.name != "nt":
        return "bash"
    candidates = []
    try:
        p = subprocess.run(["git", "--exec-path"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=True)
        git_root = os.path.abspath(os.path.join(p.stdout.strip(), "..", "..", ".."))
        candidates.append(os.path.join(git_root, "usr", "bin", "bash.exe"))
    except Exception:
        pass
    candidates.extend([
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ])
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return "bash"


BASH = bash_exe()


def python3_shim_dir():
    if os.name != "nt":
        return None
    d = os.path.join(tempfile.gettempdir(), "vemo_eval_python3_shim")
    os.makedirs(d, exist_ok=True)
    exe = sys.executable.replace("\\", "/")
    sh = os.path.join(d, "python3")
    cmd = os.path.join(d, "python3.cmd")
    open(sh, "w", encoding="utf-8").write(
        "#!/usr/bin/env sh\n"
        "if command -v cygpath >/dev/null 2>&1 && [ \"$#\" -gt 0 ]; then\n"
        "  first=\"$1\"\n"
        "  case \"$first\" in\n"
        "    /*) first=\"$(cygpath -w \"$first\")\"; shift; exec \"" + exe + "\" \"$first\" \"$@\" ;;\n"
        "  esac\n"
        "fi\n"
        "exec \"" + exe + "\" \"$@\"\n"
    )
    open(cmd, "w", encoding="utf-8").write(f'@echo off\r\n"{sys.executable}" %*\r\n')
    try:
        os.chmod(sh, 0o755)
    except OSError:
        pass
    return d


PYTHON3_SHIM = python3_shim_dir()


def child_env(**extra):
    env = dict(os.environ)
    path_parts = []
    if PYTHON3_SHIM:
        path_parts.append(PYTHON3_SHIM)
    if os.name == "nt" and os.path.isabs(BASH):
        git_usr = os.path.dirname(BASH)
        git_root = os.path.abspath(os.path.join(git_usr, "..", ".."))
        path_parts.extend([git_usr, os.path.join(git_root, "bin")])
    if path_parts:
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
    env.update(extra)
    return env

GROUPS = ("validator", "ci", "hook", "git", "budget", "auto", "skill", "fleet")

CHECK_INDEX = (
    ("validator", "scope: in-scope allowed"),
    ("validator", "scope: out-of-scope flagged"),
    ("validator", "tier: README->R0"),
    ("validator", "tier: src/app->R1"),
    ("validator", "tier: src/core->R2"),
    ("validator", "tier: unmatched (Makefile) -> R1 fail-safe"),
    ("validator", "tier: enforcement/** is R2 (self-protection)"),
    ("validator", "tier: .claude/settings.json is R2 (self-protection)"),
    ("validator", "tier: vemo.config.yaml is R2 (self-protection)"),
    ("validator", "tier: specs/** is R2 (self-protection)"),
    ("validator", "monotonic: frontier verifiers > high"),
    ("validator", "exec-evidence: 'passed' w/o run trace BLOCKED"),
    ("validator", "exec-evidence: FAKE evidence path BLOCKED"),
    ("validator", "exec-evidence: real evidence file ok"),
    ("validator", "R0: acceptance-before-push exempt"),
    ("validator", "receipt: build configured + NO receipt -> BLOCKED"),
    ("validator", "receipt: verify-run executes and passes"),
    ("validator", "receipt: gate ok after verify-run"),
    ("validator", "receipt: failing build -> receipt-failed BLOCKED"),
    ("ci", "ci workflow: verify-run before pre-push (enforcement/ci/vemo-ci.yml)"),
    ("ci", "ci workflow: verify-run before pre-push (.github/workflows/vemo-ci.yml)"),
    ("validator", "judge: front-matter pass w/o provenance BLOCKED"),
    ("validator", "judge: high-tier R2 one pass still BLOCKED"),
    ("validator", "judge: high-tier R2 required pass count ok"),
    ("validator", "judge: provenance says FAIL, front-matter says pass -> BLOCKED"),
    ("validator", "judge: low-tier R1 requires provenance"),
    ("validator", "judge: low-tier R1 one pass ok"),
    ("validator", "judge: high-tier R1 self-verifies (no judge required)"),
    ("validator", "parser: inline-map acceptance parses (spec example)"),
    ("validator", "parser: inline-map gate-check does not crash"),
    ("validator", "bind: unbound falls back to freshest heartbeat (TB)"),
    ("validator", "bind: bound session checked against ITS OWN task"),
    ("validator", "bind: bound session out-of-scope on the other task's area"),
    ("validator", "safety_invariant_of_capability=true"),
    ("validator", "rule-of-two: 3/3 trifecta BLOCKED"),
    ("validator", "rule-of-two: 2/3 allowed"),
    ("validator", "context: brief <=20 lines with tier/task/gates/budget"),
    ("validator", "judge-brief: dossier has CLAIMS/GATES/LENS (git-less sandbox degrades)"),
    ("validator", "heartbeat: stamps the task file in place"),
    ("validator", "multi-task acceptance: unaccepted sibling in range BLOCKED"),
    ("validator", "tier: .gitignore is R2 (audit visibility)"),
    ("hook", "hook e2e: in-scope edit exit 0"),
    ("hook", "hook e2e: out-of-scope edit exit 2 + reason"),
    ("hook", "hook e2e: binary blob exit 2 (safety#6)"),
    ("hook", "hook e2e: secret content exit 2 (safety#5)"),
    ("hook", "hook e2e: destructive command exit 2 (safety#4)"),
    ("hook", "hook e2e: benign command exit 0"),
    ("hook", "hook e2e: out-of-repo write exit 2"),
    ("hook", "hook e2e: git --no-verify gate evasion exit 2"),
    ("hook", "hook e2e: write into .git/ (hook tamper) exit 2"),
    ("hook", "hook e2e: core.hooksPath redirect exit 2"),
    ("hook", "hook e2e: reading .git/hooks allowed (no false positive)"),
    ("hook", "hook e2e: foreign minimal payload in-scope exit 0"),
    ("hook", "hook e2e: foreign minimal payload out-of-scope exit 2"),
    ("hook", "hook e2e: session-start exit 0 + orientation"),
    ("hook", "hook e2e: telemetry recorded blocks + session_start"),
    ("git", "pre-commit e2e: staged secret exits 1 + reason"),
    ("git", "pre-commit e2e: range with multiple task scopes exits 0"),
    ("hook", "hook e2e: task-approved destructive cmd allowed + logged"),
    ("hook", "hook e2e: Stop below acceptance -> exit 0 + reminder"),
    ("hook", "hook e2e: Stop/SubagentStop telemetry recorded"),
    ("hook", "hook e2e: monitor mode logs but does NOT block"),
    ("budget", "hook e2e: budget exceeded + auto ON -> hard stop exit 2"),
    ("budget", "hook e2e: budget exceeded + human present -> advisory exit 0"),
    ("budget", "budget: files counts writes only (1, not 2)"),
    ("budget", "stuck-loop: 3x same Bash + human -> advisory exit 0"),
    ("budget", "stuck-loop: 3x same Bash + auto ON -> hard stop exit 2"),
    ("auto", "auto: enable w/o TTY REFUSED (agent cannot self-enable)"),
    ("skill", "skill-score: VEMO's own skills pass the quality bar"),
    ("skill", "skill-audit: catalog<->disk parity + no dangling backing scripts"),
    ("skill", "skill_check selftest passes"),
    ("skill", "skill-roster lists the on-disk skills"),
    ("fleet", "fleet unit suite passes"),
    ("hook", "hook e2e: dangerous cmd quoted in echo NOT blocked (data-region)"),
    ("hook", "hook e2e: backslash-escaped heredoc opener cannot hide a real destructive command"),
    ("hook", "hook e2e: cross-line-quote comment carrier cannot hide a real destructive command"),
    ("hook", "hook e2e: danger after a bare & (background op) cannot hide behind an echo"),
    ("hook", "hook e2e: false-heredoc marker cannot hide a real destructive command"),
    ("hook", "hook e2e: commented-out heredoc opener cannot hide a real destructive command"),
    ("hook", "hook: strip_data_regions selftest passes"),
    ("hook", "hook e2e: git push -f (short force flag) blocked"),
    ("hook", "hook e2e: rm split/long recursive-force flags blocked"),
    ("hook", "hook e2e: rm -rf relative ./ target blocked"),
    ("hook", "hook e2e: git push --force-with-lease allowed (no over-block)"),
)

SECRET_FIXTURE = 'api_' + 'key = "' + 'sk-' + '0123456789abcdef0123' + '"'


class FailFast(Exception):
    """Stop the runner after the first selected failure."""


class Runner:
    """@Codex-comment
    Input: Parsed CLI filters and check metadata.
    Output: Selected check results and JSON/stdout reports.
    Key Steps: Normalize filters, record selected checks, compact stdout by default.
    Key Params: group filters, substring match filters, verbose/failures-only/fail-fast flags.
    State/Dependencies: Writes eval/out/report.json under ROOT.
    """

    def __init__(self, args):
        self.args = args
        self.selected = [
            (group, name) for group, name in CHECK_INDEX
            if self._matches(group, name)
        ]
        self.selected_set = set(self.selected)
        self.selected_groups = {group for group, _ in self.selected}
        self.checks = []

    def _matches(self, group, name):
        if self.args.group and group not in self.args.group:
            return False
        if self.args.match:
            lowered = name.lower()
            return any(term in lowered for term in self.args.match)
        return True

    def has_group(self, group):
        return group in self.selected_groups

    def chk(self, group, name, got, want):
        if (group, name) not in self.selected_set:
            return
        ok = (want in got) if isinstance(want, str) else bool(want(got))
        self.checks.append({"group": group, "name": name, "pass": ok, "got": got})
        if self.args.fail_fast and not ok:
            raise FailFast

    def report(self):
        passed = sum(1 for row in self.checks if row["pass"])
        total = len(self.checks)
        rate = round(passed / total, 3) if total else 0.0
        rep = {
            "passed": passed,
            "total": total,
            "rate": rate,
            "filters": {
                "group": sorted(self.args.group),
                "match": sorted(self.args.match),
            },
            "checks": [
                {
                    "group": row["group"],
                    "name": row["name"],
                    "pass": row["pass"],
                    "got": (row["got"] if isinstance(row["got"], str) else str(row["got"]))[:200],
                }
                for row in self.checks
            ],
        }
        out = os.path.join(ROOT, "eval", "out")
        os.makedirs(out, exist_ok=True)
        json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
        self._print(rep)
        return 0 if total and passed == total else 1

    def _print(self, rep):
        rows = self.checks
        if self.args.verbose:
            printable = rows
        elif self.args.failures_only:
            printable = [row for row in rows if not row["pass"]]
        else:
            printable = [row for row in rows if not row["pass"]]

        for row in printable:
            status = "PASS" if row["pass"] else "FAIL"
            print(f"  [{status}] {row['group']}: {row['name']}")
            if not row["pass"]:
                got = row["got"] if isinstance(row["got"], str) else str(row["got"])
                print(f"         got: {got[:200]}")

        pct = int(rep["rate"] * 100) if rep["total"] else 0
        print(f"[eval] conformance {rep['passed']}/{rep['total']} = {pct}%  -> eval/out/report.json")


def parse_args(argv):
    """Parse compact-output and check-selection options."""
    p = argparse.ArgumentParser(description="Run VEMO conformance checks.")
    p.add_argument("--group", action="append", default=[], help="Run one group; repeat or comma-separate.")
    p.add_argument("--match", action="append", default=[], help="Only count checks whose name contains text.")
    p.add_argument("--verbose", action="store_true", help="Print every selected PASS/FAIL line.")
    p.add_argument("--failures-only", action="store_true", help="Print failed selected checks plus summary.")
    p.add_argument("--fail-fast", action="store_true", help="Stop after the first selected failure.")
    p.add_argument("--list", action="store_true", help="List selected checks without running them.")
    args = p.parse_args(argv)
    args.group = normalize_csv(args.group)
    args.match = normalize_csv(args.match, lower=True)
    unknown = sorted(set(args.group) - set(GROUPS))
    if unknown:
        p.error("unknown group(s): " + ", ".join(unknown))
    return args


def normalize_csv(values, lower=False):
    out = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                out.append(part.lower() if lower else part)
    return set(out)


def run(root, *args):
    env = child_env(VEMO_ROOT=root)
    return subprocess.run([sys.executable, VAL, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env).stdout.strip()


def hook(root, guard, payload):
    """Feed a real hook payload to the dispatcher; return (exit_code, stderr)."""
    env = child_env(VEMO_ROOT=root)
    p = subprocess.run([sys.executable, HOOK, guard], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return p.returncode, p.stderr.strip()


def sandbox(tier=None, task=None, cfg_sub=()):
    d = tempfile.mkdtemp(prefix="vemo_eval_")
    cfg = open(os.path.join(ROOT, "vemo.config.yaml"), encoding="utf-8").read()
    cfg = re.sub(r'(?m)^(\s*build:\s*).*$', r'\1""', cfg, count=1)
    cfg = re.sub(r'(?m)^(\s*smoke:\s*).*$', r'\1""', cfg, count=1)
    if tier:
        cfg = re.sub(r'(?m)^(\s*tier:\s*)\w+', r'\1' + tier, cfg, count=1)
    for pat, repl in cfg_sub:
        cfg = re.sub(pat, repl, cfg, count=1)
    open(os.path.join(d, "vemo.config.yaml"), "w", encoding="utf-8").write(cfg)
    os.makedirs(os.path.join(d, "tasks"))
    os.makedirs(os.path.join(d, ".vemo"))
    if task:
        open(os.path.join(d, "tasks", "T.md"), "w", encoding="utf-8").write(task)
    return d


def remove_tree(path):
    """Best-effort temp cleanup that handles read-only Git object files on Windows."""
    def retry(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=retry)


def task(scope, state="ImplementationDone", risk="R1", status="not_run", build_exit="null",
         evidence="", trifecta="[]", verdict="null", approved="[]", task_id="T"):
    return (f"---\nid: {task_id}\nrisk: {risk}\nstate: {state}\nscope_in: {scope}\ntrifecta: {trifecta}\n"
            f"acceptance:\n  status: {status}\n  build_exit: {build_exit}\n  smoke_exit: 0\n  evidence: \"{evidence}\"\n"
            f"judge:\n  required: false\n  verdict: {verdict}\napproved_commands: {approved}\n"
            f"owning_chat: c\nheartbeat: 2026-06-16T20:00\n---\n")


def vnum(s):
    m = re.search(r"verifiers=(\d+)", s)
    return int(m.group(1)) if m else -1


def edit_payload(path, content="x = 1\n"):
    return {"tool_name": "Edit", "session_id": "eval-s1", "tool_input": {"file_path": path, "new_string": content}}


def install_precommit_fixture(root):
    """Install just enough of the git backstop into a sandbox to exercise staged-diff checks."""
    os.makedirs(os.path.join(root, "enforcement", "validators"), exist_ok=True)
    os.makedirs(os.path.join(root, "enforcement", "ci"), exist_ok=True)
    shutil.copy2(VAL, os.path.join(root, "enforcement", "validators", "task_state.py"))
    shutil.copy2(os.path.join(ROOT, "enforcement", "ci", "pre-commit"),
                 os.path.join(root, "enforcement", "ci", "pre-commit"))
    subprocess.run(["git", "init"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def run_validator_checks(r):
    if not r.has_group("validator"):
        return

    d = sandbox(task=task('["src/feature/**"]'))
    r.chk("validator", "scope: in-scope allowed", run(d, "scope-check", "--path", d + "/src/feature/x.py"), "in-scope")
    r.chk("validator", "scope: out-of-scope flagged", run(d, "scope-check", "--path", d + "/src/other/x.py"), "out-of-scope")
    remove_tree(d)

    d = sandbox()
    r.chk("validator", "tier: README->R0", run(d, "tier-required", "--paths", "README.md"), lambda g: g == "R0")
    r.chk("validator", "tier: src/app->R1", run(d, "tier-required", "--paths", "src/app/x.ts"), lambda g: g == "R1")
    r.chk("validator", "tier: src/core->R2", run(d, "tier-required", "--paths", "src/core/k.cpp"), lambda g: g == "R2")
    r.chk("validator", "tier: unmatched (Makefile) -> R1 fail-safe", run(d, "tier-required", "--paths", "Makefile"), lambda g: g == "R1")
    r.chk("validator", "tier: enforcement/** is R2 (self-protection)", run(d, "tier-required", "--paths", "enforcement/hooks/run.py"), lambda g: g == "R2")
    r.chk("validator", "tier: .claude/settings.json is R2 (self-protection)", run(d, "tier-required", "--paths", ".claude/settings.json"), lambda g: g == "R2")
    r.chk("validator", "tier: vemo.config.yaml is R2 (self-protection)", run(d, "tier-required", "--paths", "vemo.config.yaml"), lambda g: g == "R2")
    r.chk("validator", "tier: specs/** is R2 (self-protection)", run(d, "tier-required", "--paths", "specs/safety.spec.md"), lambda g: g == "R2")
    remove_tree(d)

    dh, df = sandbox(tier="high"), sandbox(tier="frontier")
    r.chk("validator", "monotonic: frontier verifiers > high", "",
          lambda _: vnum(run(df, "verify-plan", "--risk", "R2")) > vnum(run(dh, "verify-plan", "--risk", "R2")))
    remove_tree(dh)
    remove_tree(df)

    d = sandbox(task=task('["src/**"]', state="AcceptancePassed", risk="R1", status="passed"))
    r.chk("validator", "exec-evidence: 'passed' w/o run trace BLOCKED", run(d, "gate-check", "--gate", "acceptance-before-push"), "executed-evidence-missing")
    remove_tree(d)
    d = sandbox(task=task('["src/**"]', state="AcceptancePassed", risk="R1", status="passed",
                          build_exit="0", evidence="totally/fake/nonexistent.log"))
    r.chk("validator", "exec-evidence: FAKE evidence path BLOCKED", run(d, "gate-check", "--gate", "acceptance-before-push"), "evidence-file-missing")
    remove_tree(d)
    d = sandbox(task=task('["src/**"]', state="AcceptancePassed", risk="R1", status="passed",
                          build_exit="0", evidence=".vemo/run/1.log"))
    os.makedirs(os.path.join(d, ".vemo", "run"))
    open(os.path.join(d, ".vemo", "run", "1.log"), "w", encoding="utf-8").write("$ true\n[exit 0]\n")
    r.chk("validator", "exec-evidence: real evidence file ok", run(d, "gate-check", "--gate", "acceptance-before-push"), lambda g: g == "ok")
    remove_tree(d)

    d = sandbox(task=task('["docs/**"]', state="ImplementationDone", risk="R0"))
    r.chk("validator", "R0: acceptance-before-push exempt", run(d, "gate-check", "--gate", "acceptance-before-push"), lambda g: g == "ok")
    remove_tree(d)

    d = sandbox(task=task('["src/**"]', state="AcceptancePassed", risk="R1", status="passed",
                          build_exit="0", evidence=".vemo/run/1.log"),
                cfg_sub=[(r'(?m)^(\s*build:\s*)""', r'\1python3 .vemo/run/pass_build.py')])
    os.makedirs(os.path.join(d, ".vemo", "run"))
    open(os.path.join(d, ".vemo", "run", "1.log"), "w", encoding="utf-8").write("x\n")
    open(os.path.join(d, ".vemo", "run", "pass_build.py"), "w", encoding="utf-8").write("import sys\nsys.exit(0)\n")
    r.chk("validator", "receipt: build configured + NO receipt -> BLOCKED", run(d, "gate-check", "--gate", "acceptance-before-push"), "no-verify-receipt")
    r.chk("validator", "receipt: verify-run executes and passes", run(d, "verify-run"), lambda g: g.startswith("pass"))
    r.chk("validator", "receipt: gate ok after verify-run", run(d, "gate-check", "--gate", "acceptance-before-push"), lambda g: g == "ok")
    remove_tree(d)
    d = sandbox(task=task('["src/**"]', state="AcceptancePassed", risk="R1", status="passed",
                          build_exit="0", evidence=".vemo/run/1.log"),
                cfg_sub=[(r'(?m)^(\s*build:\s*)""', r'\1python3 .vemo/run/fail_build.py')])
    os.makedirs(os.path.join(d, ".vemo", "run"))
    open(os.path.join(d, ".vemo", "run", "1.log"), "w", encoding="utf-8").write("x\n")
    open(os.path.join(d, ".vemo", "run", "fail_build.py"), "w", encoding="utf-8").write("import sys\nsys.exit(1)\n")
    run(d, "verify-run")
    r.chk("validator", "receipt: failing build -> receipt-failed BLOCKED", run(d, "gate-check", "--gate", "acceptance-before-push"), "receipt-failed")
    remove_tree(d)

    d = sandbox(task=task('["src/**"]', risk="R2", verdict="pass"))
    r.chk("validator", "judge: front-matter pass w/o provenance BLOCKED", run(d, "gate-check", "--gate", "r2-judge"), "judge-no-provenance")
    run(d, "judge-record", "--task", "T", "--verdict", "pass", "--evidence", "e2e")
    r.chk("validator", "judge: high-tier R2 one pass still BLOCKED", run(d, "gate-check", "--gate", "required-judge"), "judge-pass-count=1")
    run(d, "judge-record", "--task", "T", "--verdict", "pass", "--evidence", "e2e-2")
    r.chk("validator", "judge: high-tier R2 required pass count ok", run(d, "gate-check", "--gate", "required-judge"), lambda g: g == "ok")
    remove_tree(d)
    d = sandbox(task=task('["src/**"]', risk="R2", verdict="pass"))
    run(d, "judge-record", "--task", "T", "--verdict", "fail")
    r.chk("validator", "judge: provenance says FAIL, front-matter says pass -> BLOCKED", run(d, "gate-check", "--gate", "r2-judge"), "judge-provenance-mismatch")
    remove_tree(d)
    d = sandbox(tier="low", task=task('["src/**"]', risk="R1", verdict="pass"))
    r.chk("validator", "judge: low-tier R1 requires provenance", run(d, "gate-check", "--gate", "required-judge"), "judge-no-provenance")
    run(d, "judge-record", "--task", "T", "--verdict", "pass", "--evidence", "low-r1")
    r.chk("validator", "judge: low-tier R1 one pass ok", run(d, "gate-check", "--gate", "required-judge"), lambda g: g == "ok")
    remove_tree(d)
    d = sandbox(tier="high", task=task('["src/**"]', risk="R1", verdict="null"))
    r.chk("validator", "judge: high-tier R1 self-verifies (no judge required)", run(d, "gate-check", "--gate", "required-judge"), lambda g: g == "ok")
    remove_tree(d)

    d = sandbox(task=("---\nid: T\nrisk: R1\nstate: ImplementationDone\nscope_in: [\"src/**\"]\n"
                      "acceptance: { status: passed, build_exit: 0, smoke_exit: 0, evidence: \".vemo/run/1.log\" }\n"
                      "judge: { required: false, verdict: null }\nowning_chat: c\nheartbeat: 2026-06-16T20:00\n---\n"))
    r.chk("validator", "parser: inline-map acceptance parses (spec example)", run(d, "get", "--field", "acceptance.status"), lambda g: g == "passed")
    r.chk("validator", "parser: inline-map gate-check does not crash", run(d, "gate-check", "--gate", "acceptance-before-push"), "block:")
    remove_tree(d)

    d = sandbox(task=task('["src/a/**"]', task_id="TA"))
    open(os.path.join(d, "tasks", "T2.md"), "w", encoding="utf-8").write(
        task('["src/b/**"]', task_id="TB").replace("heartbeat: 2026-06-16T20:00", "heartbeat: 2026-06-17T09:00"))
    r.chk("validator", "bind: unbound falls back to freshest heartbeat (TB)", run(d, "scope-check", "--path", d + "/src/b/x.py"), "in-scope")
    run(d, "bind", "--session", "s-A", "--task", "TA")
    r.chk("validator", "bind: bound session checked against ITS OWN task", run(d, "scope-check", "--path", d + "/src/a/x.py", "--session", "s-A"), "in-scope")
    r.chk("validator", "bind: bound session out-of-scope on the other task's area", run(d, "scope-check", "--path", d + "/src/b/x.py", "--session", "s-A"), "out-of-scope")
    remove_tree(d)

    d = sandbox()
    r.chk("validator", "safety_invariant_of_capability=true", run(d, "config-get", "--field", "enforcement.safety_invariant_of_capability"), lambda g: str(g).lower() == "true")
    remove_tree(d)
    d = sandbox(task=task('["src/**"]', trifecta='[private_data, untrusted_content, external_comms]'))
    r.chk("validator", "rule-of-two: 3/3 trifecta BLOCKED", run(d, "trifecta-check"), "block:rule-of-two")
    remove_tree(d)
    d = sandbox(task=task('["src/**"]', trifecta='[private_data, external_comms]'))
    r.chk("validator", "rule-of-two: 2/3 allowed", run(d, "trifecta-check"), lambda g: g.startswith("ok"))
    remove_tree(d)

    d = sandbox(task=task('["src/**"]'))
    brief = run(d, "context")
    r.chk("validator", "context: brief <=20 lines with tier/task/gates/budget", brief,
          lambda g: len(g.splitlines()) <= 20 and "tier=" in g and "task T" in g
          and "gate acceptance-before-push:" in g and "budget:" in g)
    dossier = run(d, "judge-brief", "--lens", "safety")
    r.chk("validator", "judge-brief: dossier has CLAIMS/GATES/LENS (git-less sandbox degrades)", dossier,
          lambda g: "CLAIMS:" in g and "GATES" in g and "LENS safety" in g and "RULES:" in g)
    hb = run(d, "heartbeat")
    body = open(os.path.join(d, "tasks", "T.md"), encoding="utf-8").read()
    r.chk("validator", "heartbeat: stamps the task file in place", hb,
          lambda g: g.startswith("heartbeat:T=") and "heartbeat: 2026-06-16T20:00" not in body)
    remove_tree(d)

    d = sandbox(task=task('["src/a/**"]', state="AcceptancePassed", risk="R1", status="passed",
                          build_exit="0", evidence=".vemo/run/1.log", task_id="TA"))
    os.makedirs(os.path.join(d, ".vemo", "run"))
    open(os.path.join(d, ".vemo", "run", "1.log"), "w", encoding="utf-8").write("ok\n")
    open(os.path.join(d, "tasks", "T2.md"), "w", encoding="utf-8").write(task('["src/b/**"]', task_id="TB"))
    r.chk("validator", "multi-task acceptance: unaccepted sibling in range BLOCKED",
          run(d, "gate-check", "--gate", "acceptance-before-push",
              "--task-file", "tasks/T.md", "--task-file", "tasks/T2.md"),
          lambda g: g.startswith("block:") and "[task=TB]" in g)
    remove_tree(d)

    d = sandbox()
    r.chk("validator", "tier: .gitignore is R2 (audit visibility)",
          run(d, "tier-required", "--paths", ".gitignore"), lambda g: g == "R2")
    remove_tree(d)


def run_ci_checks(r):
    if not r.has_group("ci"):
        return
    for wf in ("enforcement/ci/vemo-ci.yml", ".github/workflows/vemo-ci.yml"):
        txt = open(os.path.join(ROOT, wf), encoding="utf-8").read()
        r.chk("ci", f"ci workflow: verify-run before pre-push ({wf})", txt,
              lambda g: "verify-run" in g and "bash enforcement/ci/pre-push" in g
              and g.index("verify-run") < g.index("bash enforcement/ci/pre-push"))


def run_hook_checks(r):
    if not r.has_group("hook"):
        return
    d = sandbox(task=task('["src/feature/**"]'))
    rc, err = hook(d, "edit", edit_payload(d + "/src/feature/x.py"))
    r.chk("hook", "hook e2e: in-scope edit exit 0", (rc, err), lambda g: g[0] == 0)
    rc, err = hook(d, "edit", edit_payload(d + "/src/other/x.py"))
    r.chk("hook", "hook e2e: out-of-scope edit exit 2 + reason", (rc, err), lambda g: g[0] == 2 and "safety.spec#1" in g[1])
    rc, err = hook(d, "edit", edit_payload(d + "/src/feature/model.onnx"))
    r.chk("hook", "hook e2e: binary blob exit 2 (safety#6)", (rc, err), lambda g: g[0] == 2 and "safety.spec#6" in g[1])
    rc, err = hook(d, "edit", edit_payload(d + "/src/feature/cfg.py", SECRET_FIXTURE))
    r.chk("hook", "hook e2e: secret content exit 2 (safety#5)", (rc, err), lambda g: g[0] == 2 and "safety.spec#5" in g[1])
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "git reset --hard HEAD~3"}})
    r.chk("hook", "hook e2e: destructive command exit 2 (safety#4)", (rc, err), lambda g: g[0] == 2 and "safety.spec#4" in g[1])
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    r.chk("hook", "hook e2e: benign command exit 0", (rc, err), lambda g: g[0] == 0)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "echo pwned >> ~/.bashrc"}})
    r.chk("hook", "hook e2e: out-of-repo write exit 2", (rc, err), lambda g: g[0] == 2 and "outside repo_root" in g[1])
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "git commit -m wip --no-verify"}})
    r.chk("hook", "hook e2e: git --no-verify gate evasion exit 2", (rc, err), lambda g: g[0] == 2 and "no-verify" in g[1])
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "echo ok > .git/hooks/pre-push"}})
    r.chk("hook", "hook e2e: write into .git/ (hook tamper) exit 2", (rc, err), lambda g: g[0] == 2 and "git-gate tamper" in g[1])
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "git config core.hooksPath /tmp/nohooks"}})
    r.chk("hook", "hook e2e: core.hooksPath redirect exit 2", (rc, err), lambda g: g[0] == 2 and "hooksPath" in g[1])
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "ls -la .git/hooks"}})
    r.chk("hook", "hook e2e: reading .git/hooks allowed (no false positive)", (rc, err), lambda g: g[0] == 0)
    rc, err = hook(d, "edit", {"tool_name": "Write", "tool_input": {"file_path": d + "/src/feature/ok.py"}, "harness": "generic"})
    r.chk("hook", "hook e2e: foreign minimal payload in-scope exit 0", (rc, err), lambda g: g[0] == 0)
    rc, err = hook(d, "edit", {"tool_name": "Write", "tool_input": {"file_path": d + "/src/other/no.py"}, "harness": "generic"})
    r.chk("hook", "hook e2e: foreign minimal payload out-of-scope exit 2", (rc, err), lambda g: g[0] == 2 and "safety.spec#1" in g[1])
    rc, err = hook(d, "session-start", {"session_id": "eval-s1"})
    r.chk("hook", "hook e2e: session-start exit 0 + orientation", (rc, err), lambda g: g[0] == 0)
    tele = open(os.path.join(d, ".vemo", "telemetry.jsonl"), encoding="utf-8").read()
    r.chk("hook", "hook e2e: telemetry recorded blocks + session_start", tele,
          lambda g: "scope_block_out" in g and "session_start" in g)
    remove_tree(d)

    d = sandbox(task=task('["src/**"]', approved='["git reset --hard HEAD~1"]'))
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "git reset --hard HEAD~1"}})
    r.chk("hook", "hook e2e: task-approved destructive cmd allowed + logged", (rc, err), lambda g: g[0] == 0)
    remove_tree(d)

    d = sandbox(task=task('["src/**"]'))
    rc, err = hook(d, "stop", {"session_id": "eval-s1"})
    r.chk("hook", "hook e2e: Stop below acceptance -> exit 0 + reminder", (rc, err), lambda g: g[0] == 0 and "not yet push-ready" in g[1])
    rc2, _ = hook(d, "subagent-stop", {"session_id": "eval-s1"})
    tele = open(os.path.join(d, ".vemo", "telemetry.jsonl"), encoding="utf-8").read()
    r.chk("hook", "hook e2e: Stop/SubagentStop telemetry recorded", tele,
          lambda g: rc2 == 0 and "stop_below_acceptance" in g and "subagent_stop" in g)
    remove_tree(d)

    d = sandbox(task=task('["src/feature/**"]'), cfg_sub=[(r'(?m)^(\s*mode:\s*)enforce', r'\1monitor')])
    rc, err = hook(d, "edit", edit_payload(d + "/src/other/x.py"))
    r.chk("hook", "hook e2e: monitor mode logs but does NOT block", (rc, err), lambda g: g[0] == 0 and "monitor mode" in g[1])
    remove_tree(d)

    # data-region precision (strip_data_regions): a dangerous command that is only QUOTED 鈥?in an echo,
    # a quoted-delimiter heredoc body, or a comment line the agent is writing 鈥?must NOT hard-block,
    # while a really-executed one still does (covered by the destructive-command check above).
    d = sandbox(task=task('["src/**"]'))
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "echo 'to undo, run git reset --hard'"}})
    r.chk("hook", "hook e2e: dangerous cmd quoted in echo NOT blocked (data-region)", (rc, err), lambda g: g[0] == 0)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "cat \\<<'W'\ngit reset --hard HEAD~9\nW"}})
    r.chk("hook", "hook e2e: backslash-escaped heredoc opener cannot hide a real destructive command", (rc, err), lambda g: g[0] == 2)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "eval '\n#c' ; rm -rf /"}})
    r.chk("hook", "hook e2e: cross-line-quote comment carrier cannot hide a real destructive command", (rc, err), lambda g: g[0] == 2)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "echo x & rm -rf /"}})
    r.chk("hook", "hook e2e: danger after a bare & (background op) cannot hide behind an echo", (rc, err), lambda g: g[0] == 2)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": 'echo "see <<\'EOF\'"\ngit reset --hard HEAD~9\nEOF'}})
    r.chk("hook", "hook e2e: false-heredoc marker cannot hide a real destructive command", (rc, err), lambda g: g[0] == 2)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "# cleanup: <<'W'\ngit reset --hard HEAD~9\nW"}})
    r.chk("hook", "hook e2e: commented-out heredoc opener cannot hide a real destructive command", (rc, err), lambda g: g[0] == 2)
    remove_tree(d)
    sp = subprocess.run([sys.executable, HOOK, "--selftest"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    r.chk("hook", "hook: strip_data_regions selftest passes", (sp.returncode, sp.stdout),
          lambda g: g[0] == 0 and "selftest OK" in g[1])

    # DESTRUCTIVE hardening (v1.9.0): short/split/long force+recursive flags and ./relative targets are
    # caught; --force-with-lease stays allowed (tightening = safe direction, but must not over-block).
    d = sandbox(task=task('["src/**"]'))
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "git push -f origin main"}})
    r.chk("hook", "hook e2e: git push -f (short force flag) blocked", (rc, err), lambda g: g[0] == 2)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "rm -r -f /tmp/xdir"}})
    r.chk("hook", "hook e2e: rm split/long recursive-force flags blocked", (rc, err), lambda g: g[0] == 2)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "rm -rf ./buildXYZ"}})
    r.chk("hook", "hook e2e: rm -rf relative ./ target blocked", (rc, err), lambda g: g[0] == 2)
    rc, err = hook(d, "command", {"tool_name": "Bash", "tool_input": {"command": "git push --force-with-lease origin main"}})
    r.chk("hook", "hook e2e: git push --force-with-lease allowed (no over-block)", (rc, err), lambda g: g[0] == 0)
    remove_tree(d)


def run_git_checks(r):
    if not r.has_group("git"):
        return
    d = sandbox(task=task('["src/**"]', state="AcceptancePassed", risk="R1", status="passed",
                          build_exit="0", evidence=".vemo/run/1.log"))
    os.makedirs(os.path.join(d, ".vemo", "run"))
    open(os.path.join(d, ".vemo", "run", "1.log"), "w", encoding="utf-8").write("ok\n")
    install_precommit_fixture(d)
    os.makedirs(os.path.join(d, "src"))
    open(os.path.join(d, "src", "secret.py"), "w", encoding="utf-8").write(SECRET_FIXTURE + "\n")
    subprocess.run(["git", "add", "src/secret.py"], cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    p = subprocess.run([BASH, "enforcement/ci/pre-commit"], cwd=d, capture_output=True, text=True,
                       env=child_env(),
                       encoding="utf-8", errors="replace")
    r.chk("git", "pre-commit e2e: staged secret exits 1 + reason", p.stdout + p.stderr,
          lambda g: p.returncode == 1 and "secret-scan" in g)
    remove_tree(d)

    d = sandbox()
    install_precommit_fixture(d)
    subprocess.run(["git", "config", "user.email", "eval@example.invalid"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "VEMO Eval"], cwd=d, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "base"], cwd=d,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
    os.makedirs(os.path.join(d, "src", "core"), exist_ok=True)
    open(os.path.join(d, "tasks", "T1.md"), "w", encoding="utf-8").write(
        task('["src/core/**", "tasks/T1.md", ".vemo/judge.jsonl"]', state="AcceptancePassed", risk="R2",
             status="passed", build_exit="0", evidence=".vemo/run/1.log", verdict="pass", task_id="T1"))
    open(os.path.join(d, "src", "core", "x.py"), "w", encoding="utf-8").write("x = 1\n")
    run(d, "judge-record", "--task", "T1", "--verdict", "pass", "--evidence", "r2-1")
    run(d, "judge-record", "--task", "T1", "--verdict", "pass", "--evidence", "r2-2")
    subprocess.run(["git", "add", "tasks/T1.md", "src/core/x.py", ".vemo/judge.jsonl"], cwd=d,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    subprocess.run(["git", "commit", "-m", "r2 task"], cwd=d,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    os.makedirs(os.path.join(d, "eval"), exist_ok=True)
    open(os.path.join(d, "tasks", "T2.md"), "w", encoding="utf-8").write(
        task('["eval/**", "tasks/T2.md"]', state="AcceptancePassed", risk="R1",
             status="passed", build_exit="0", evidence=".vemo/run/1.log", task_id="T2"))
    open(os.path.join(d, "eval", "run.py"), "w", encoding="utf-8").write("print('ok')\n")
    subprocess.run(["git", "add", "tasks/T2.md", "eval/run.py"], cwd=d,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    subprocess.run(["git", "commit", "-m", "r1 task"], cwd=d,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    p = subprocess.run([BASH, "enforcement/ci/pre-commit"], cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env=child_env(VEMO_DIFF_RANGE=f"{base}...HEAD"))
    r.chk("git", "pre-commit e2e: range with multiple task scopes exits 0", p.stdout + p.stderr,
          lambda g: p.returncode == 0)
    remove_tree(d)


def run_budget_checks(r):
    if not r.has_group("budget"):
        return
    d = sandbox(task=task('["src/**"]'))
    json.dump({"enabled": True, "max_auto_tier": "R1"}, open(os.path.join(d, ".vemo", "auto_mode.json"), "w", encoding="utf-8"))
    json.dump({"started": "2026-06-16T20:00", "tool_calls": 9999, "files": []}, open(os.path.join(d, ".vemo", "run.json"), "w", encoding="utf-8"))
    rc, err = hook(d, "budget", {"tool_name": "Read", "tool_input": {"file_path": d + "/src/x.py"}})
    r.chk("budget", "hook e2e: budget exceeded + auto ON -> hard stop exit 2", (rc, err), lambda g: g[0] == 2 and "STOP RULE" in g[1])
    json.dump({"enabled": False}, open(os.path.join(d, ".vemo", "auto_mode.json"), "w", encoding="utf-8"))
    rc, err = hook(d, "budget", {"tool_name": "Read", "tool_input": {"file_path": d + "/src/x.py"}})
    r.chk("budget", "hook e2e: budget exceeded + human present -> advisory exit 0", (rc, err), lambda g: g[0] == 0 and "advisory" in g[1])
    remove_tree(d)

    d = sandbox(task=task('["src/**"]'))
    hook(d, "budget", {"tool_name": "Read", "tool_input": {"file_path": d + "/src/r.py"}, "session_id": "s9"})
    hook(d, "budget", {"tool_name": "Edit", "tool_input": {"file_path": d + "/src/w.py"}, "session_id": "s9"})
    st = run(d, "budget-status", "--session", "s9")
    r.chk("budget", "budget: files counts writes only (1, not 2)", st, lambda g: "files=1/" in g)
    remove_tree(d)

    same_cmd = {"tool_name": "Bash", "tool_input": {"command": "pytest tests/test_x.py"}, "session_id": "s10"}
    d = sandbox(task=task('["src/**"]'))
    hook(d, "budget", same_cmd)
    hook(d, "budget", same_cmd)
    rc, err = hook(d, "budget", same_cmd)
    r.chk("budget", "stuck-loop: 3x same Bash + human -> advisory exit 0", (rc, err),
          lambda g: g[0] == 0 and "stuck-loop" in g[1] and "advisory" in g[1])
    json.dump({"enabled": True, "max_auto_tier": "R1"}, open(os.path.join(d, ".vemo", "auto_mode.json"), "w", encoding="utf-8"))
    rc, err = hook(d, "budget", same_cmd)
    r.chk("budget", "stuck-loop: 3x same Bash + auto ON -> hard stop exit 2", (rc, err),
          lambda g: g[0] == 2 and "stuck-loop" in g[1] and "STOP RULE" in g[1])
    remove_tree(d)


def run_auto_checks(r):
    if not r.has_group("auto"):
        return
    tmp = tempfile.mkdtemp(prefix="vemo_eval_")
    try:
        p = subprocess.run([sys.executable, os.path.join(ROOT, "enforcement", "automation", "vemo-auto"), "on"],
                           input="", capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=child_env(VEMO_ROOT=tmp))
        r.chk("auto", "auto: enable w/o TTY REFUSED (agent cannot self-enable)", p.stdout, "REFUSED")
    finally:
        remove_tree(tmp)


def run_skill_checks(r):
    if not r.has_group("skill"):
        return
    sk = os.path.join(ROOT, "enforcement", "validators", "skill_check.py")

    def sc(*args):
        return subprocess.run([sys.executable, sk, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    p = sc("score", "--root", ROOT)
    r.chk("skill", "skill-score: VEMO's own skills pass the quality bar", (p.returncode, p.stdout),
          lambda g: g[0] == 0 and "skill-score: PASS" in g[1])
    p = sc("audit", "--root", ROOT)
    r.chk("skill", "skill-audit: catalog<->disk parity + no dangling backing scripts", (p.returncode, p.stdout),
          lambda g: g[0] == 0 and "skill-audit: OK" in g[1])
    p = sc("selftest")
    r.chk("skill", "skill_check selftest passes", (p.returncode, p.stdout),
          lambda g: g[0] == 0 and "selftest]" in g[1])
    p = sc("roster", "--root", ROOT)
    r.chk("skill", "skill-roster lists the on-disk skills", (p.returncode, p.stdout),
          lambda g: g[0] == 0 and "VEMO skills (" in g[1])


def run_fleet_checks(r):
    if not r.has_group("fleet"):
        return
    p = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=child_env(),
    )
    r.chk("fleet", "fleet unit suite passes", (p.returncode, p.stdout + p.stderr),
          lambda g: g[0] == 0 and "OK" in g[1])


def main(argv):
    # Hermetic sandboxes: VEMO_DIFF_RANGE is a REAL-repo concept (the pushed range). Our git checks
    # run pre-commit against throwaway `git init` sandboxes, so an inherited range points at revisions
    # that do not exist there. CI exports VEMO_DIFF_RANGE for the backstop/push-gate steps; if eval runs
    # after that (e.g. inside `vemo verify` as paths.build), the leak would break the sandbox git check.
    # Drop it here so eval behaves identically however it is invoked. (The multi-task git check sets its
    # own VEMO_DIFF_RANGE per-subprocess, so this does not weaken it.)
    os.environ.pop("VEMO_DIFF_RANGE", None)
    args = parse_args(argv)
    runner = Runner(args)
    if args.list:
        for group, name in runner.selected:
            print(f"{group}\t{name}")
        return 0 if runner.selected else 1
    if not runner.selected:
        out = os.path.join(ROOT, "eval", "out")
        os.makedirs(out, exist_ok=True)
        json.dump({"passed": 0, "total": 0, "rate": 0.0,
                   "filters": {"group": sorted(args.group), "match": sorted(args.match)},
                   "checks": [], "error": "no-checks-selected"},
                  open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
        print("[eval] no checks selected  -> eval/out/report.json")
        return 1
    try:
        run_validator_checks(runner)
        run_ci_checks(runner)
        run_hook_checks(runner)
        run_git_checks(runner)
        run_budget_checks(runner)
        run_auto_checks(runner)
        run_skill_checks(runner)
        run_fleet_checks(runner)
    except FailFast:
        pass
    return runner.report()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
