#!/usr/bin/env python3
"""govsync.py — deterministic upstream-update check for the governance-sync skill.

Governance sync should be deterministic. This tool asks the upstream what version/commit it is, compares to
local, and prints a clear verdict. It NEVER auto-applies (sync of files is a confirmed, separate step).
Stdlib + the `gh` CLI.

  govsync.py check     compare local VERSION/commit to upstream `vemo.upstream`; print what changed
  govsync.py status    show the last recorded sync state
"""
import sys, os, json, subprocess, re

ROOT = os.environ.get("VEMO_ROOT") or os.popen("git rev-parse --show-toplevel 2>/dev/null").read().strip() or "."
STATE = os.path.join(ROOT, ".vemo", "governance.json")


def _gh_ok():
    return subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0 \
        if _which("gh") else False


def _which(t):
    from shutil import which
    return which(t)


def _upstream():
    try:
        for ln in open(os.path.join(ROOT, "vemo.config.yaml"), encoding="utf-8"):
            m = re.match(r"\s*upstream:\s*[\"']?([^\"'#\s]+)", ln)
            if m and "/" in m.group(1):
                return m.group(1)
    except OSError:
        pass
    return ""


def _local_version():
    for p in ("VERSION", "vemo.config.yaml"):
        fp = os.path.join(ROOT, p)
        if os.path.exists(fp):
            for ln in open(fp, encoding="utf-8"):
                m = re.search(r"version:\s*[\"']?([0-9]+\.[0-9]+\.[0-9]+)", ln)
                if m:
                    return m.group(1)
                if p == "VERSION":
                    return ln.strip()
    return "unknown"


def _gh_json(path, jq):
    r = subprocess.run(["gh", "api", path, "--jq", jq], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def cmd_check():
    up = _upstream()
    if not up:
        print("[govsync] no `vemo.upstream` set in vemo.config.yaml — nothing to sync against."); return 0
    if not _gh_ok():
        print("[govsync] GitHub CLI not available/authenticated. Install gh + `gh auth login`, then retry. (Not blocking your session.)"); return 0
    local_v = _local_version()
    remote_v = _gh_json(f"repos/{up}/contents/VERSION", ".content")
    if remote_v:
        import base64
        try: remote_v = base64.b64decode(remote_v).decode().strip()
        except Exception: pass
    remote_c = _gh_json(f"repos/{up}/commits/HEAD", ".sha")
    last = _state().get("upstream_commit", "none")
    print(f"[govsync] upstream {up}")
    print(f"  local version : {local_v}")
    print(f"  upstream ver  : {remote_v or '?'}")
    print(f"  upstream HEAD : {(remote_c or '?')[:8]}   (last synced: {last[:8] if last!='none' else 'never'})")
    if remote_c and remote_c != last:
        print("  → UPDATE AVAILABLE. Review changes, then apply ONLY template-owned files "
              "(specs/, enforcement/, skill/) on confirmation. Never overwrite vemo.config.yaml, tasks/, .vemo/, "
              "or AGENTS.md (report-only).")
    else:
        print("  → up to date.")
    return 0


def _state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_status():
    print(json.dumps(_state(), indent=2) if _state() else "[govsync] no sync recorded yet.")
    return 0


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit(cmd_check() if c == "check" else cmd_status() if c == "status" else (print("usage: govsync.py check|status") or 2))
