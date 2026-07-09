#!/usr/bin/env python3
"""cg.py — VEMO call-graph engine (the deterministic core of the call-graph skill).

Why this exists: call-graph work should be deterministic. The tool handles indexing and graph queries, while
SKILL.md stays a thin contract. Tool-first beats prose-first, and beats a model "eyeballing" call relationships.

Commands:
  cg.py index                      build/refresh the symbol index (ctags) + call db (cscope, if present)
  cg.py def     <symbol>           Q1 where is X defined
  cg.py callers <symbol>           Q2 who calls X
  cg.py callees <symbol>           Q3 what X calls
  cg.py chain   <symbol> [--depth N]   Q4 forward call tree (default depth 3)
  cg.py impact  <symbol> [--depth N]   Q5 reverse tree (who reaches X)

Stdlib only. Degrades loudly: if a tool is missing it prints how to install it, never silently lies.
"""
import sys, os, subprocess, argparse, shutil

ROOT = os.environ.get("VEMO_ROOT") or os.popen("git rev-parse --show-toplevel 2>/dev/null").read().strip() or "."
VDIR = os.path.join(ROOT, ".vemo")
TAGS = os.path.join(VDIR, "tags")
CSCOPE = os.path.join(VDIR, "cscope.out")
EXCLUDE = {".git", ".vemo", "node_modules", "vendor", "3rdparty", "build", "dist", ".venv", "__pycache__"}
SRC_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".py", ".ts", ".js", ".go", ".rs", ".java")
INSTALL = {"ctags": "Universal Ctags — apt install universal-ctags / brew install universal-ctags / winget install universal-ctags.ctags",
           "cscope": "cscope — apt install cscope / brew install cscope"}


def _have(tool):
    return shutil.which(tool) is not None


def _src_files():
    out = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in EXCLUDE]
        for f in fns:
            if f.endswith(SRC_EXT):
                out.append(os.path.join(dp, f))
    return out


def cmd_index():
    os.makedirs(VDIR, exist_ok=True)
    if not _have("ctags"):
        print(f"[cg] ctags not found. Install: {INSTALL['ctags']}"); return 1
    ex = " ".join(f"--exclude={d}" for d in EXCLUDE)
    subprocess.run(f"ctags -R --fields=+nKsS {ex} -f {TAGS} {ROOT}", shell=True)
    print(f"[cg] ctags index -> {TAGS}")
    if _have("cscope"):
        files = [f for f in _src_files() if f.endswith((".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"))]
        if files:
            namefile = os.path.join(VDIR, "cscope.files")
            open(namefile, "w").write("\n".join(files))
            subprocess.run(f"cscope -b -q -k -i {namefile} -f {CSCOPE}", shell=True, cwd=ROOT)
            print(f"[cg] cscope db -> {CSCOPE} ({len(files)} C/C++ files)")
    else:
        print(f"[cg] (cscope absent — callers/callees use ctags approximation. {INSTALL['cscope']})")
    return 0


def _ensure_index():
    if not os.path.exists(TAGS):
        cmd_index()


def cmd_def(sym):
    _ensure_index()
    if not os.path.exists(TAGS):
        return 1
    hits = []
    for line in open(TAGS, encoding="utf-8", errors="ignore"):
        if line.startswith(sym + "\t"):
            p = line.split("\t")
            hits.append(f"{p[1]}  ({p[2].strip() if len(p) > 2 else '?'})")
    print(f"{sym} defined at:\n  " + ("\n  ".join(hits) if hits else "(not found in index)"))
    return 0


def _cscope(num, sym):
    if not (os.path.exists(CSCOPE) and _have("cscope")):
        return None
    r = subprocess.run(["cscope", "-d", "-f", CSCOPE, "-L", f"-{num}", sym],
                       capture_output=True, text=True, cwd=ROOT)
    out = []
    for ln in r.stdout.splitlines():
        parts = ln.split(None, 3)
        if len(parts) >= 3:
            out.append((parts[1], parts[0], parts[2]))   # (func, file, line)
    return out


def cmd_callers(sym):
    _ensure_index()
    res = _cscope(3, sym)
    if res is None:
        # ctags approximation: grep call sites
        hits = subprocess.run(f"grep -rnw --include='*.*' '{sym}' {ROOT} "
                              + " ".join(f"--exclude-dir={d}" for d in EXCLUDE),
                              shell=True, capture_output=True, text=True).stdout.splitlines()
        print(f"Callers of {sym}()  [ctags approx — install cscope for precision]:")
        for h in hits[:40]:
            print("  " + h)
        return 0
    print(f"Callers of {sym}():")
    for fn, fl, ln in res:
        print(f"  {fn}() [{fl}:{ln}]")
    return 0


def cmd_callees(sym):
    _ensure_index()
    res = _cscope(2, sym)
    if res is None:
        print(f"[cg] callees of {sym} need cscope (C/C++) or pyan3 (Python). {INSTALL['cscope']}")
        return 1
    print(f"{sym}() calls:")
    for fn, fl, ln in res:
        print(f"  {fn}() [{fl}:{ln}]")
    return 0


def _tree(sym, depth, num, seen, indent=""):
    if depth < 0 or sym in seen:
        return
    seen.add(sym)
    res = _cscope(num, sym) or []
    for fn, fl, ln in res:
        print(f"{indent}└─ {fn}() [{fl}:{ln}]")
        _tree(fn, depth - 1, num, seen, indent + "   ")


def cmd_chain(sym, depth):
    _ensure_index()
    if not (os.path.exists(CSCOPE) and _have("cscope")):
        print(f"[cg] call chains need cscope. {INSTALL['cscope']}"); return 1
    print(f"{sym}()  (forward, depth {depth})")
    _tree(sym, depth - 1, 2, set())
    return 0


def cmd_impact(sym, depth):
    _ensure_index()
    if not (os.path.exists(CSCOPE) and _have("cscope")):
        print(f"[cg] impact analysis needs cscope. {INSTALL['cscope']}"); return 1
    print(f"reaches {sym}()  (reverse, depth {depth})")
    _tree(sym, depth - 1, 3, set())
    return 0


def main():
    ap = argparse.ArgumentParser(prog="cg.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    for c in ("def", "callers", "callees"):
        p = sub.add_parser(c); p.add_argument("symbol")
    for c in ("chain", "impact"):
        p = sub.add_parser(c); p.add_argument("symbol"); p.add_argument("--depth", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "index":   sys.exit(cmd_index())
    if a.cmd == "def":     sys.exit(cmd_def(a.symbol))
    if a.cmd == "callers": sys.exit(cmd_callers(a.symbol))
    if a.cmd == "callees": sys.exit(cmd_callees(a.symbol))
    if a.cmd == "chain":   sys.exit(cmd_chain(a.symbol, a.depth))
    if a.cmd == "impact":  sys.exit(cmd_impact(a.symbol, a.depth))


if __name__ == "__main__":
    main()
