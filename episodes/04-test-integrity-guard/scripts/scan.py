#!/usr/bin/env python3
"""test-integrity-guard scanner: did this change make your tests check less?

Usage:
  python3 scan.py [repo] [--base REF] [--json]   compare test files against REF (default: HEAD,
                                                  i.e. your uncommitted changes; in CI use origin/main)
  python3 scan.py [repo] --snapshot [--json]     list what is switched off or can never fail today

Diff mode reports, per changed test file: assertions removed, strict checks made loose, tests
skipped, .only added, assertions that can never fail, swallowed errors; deleted test files; and
CI files / package.json that now let a red run pass. Exit code 1 when diff mode finds anything
(snapshot mode is informational and exits 0).
Read-only: it runs `git diff` and `git show`, never writes.
"""
import fnmatch
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tig_lib import file_findings, is_ci_file, is_test_file, metrics  # noqa: E402

SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", "bin", "obj", "target", "vendor", "coverage", "venv", ".venv", "__pycache__"}


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def read(p):
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def ignored(repo):
    try:
        with open(os.path.join(repo, ".testguardignore"), encoding="utf-8") as f:
            globs = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        globs = []
    return lambda rel: any(fnmatch.fnmatch(rel, g) for g in globs)


def diff_scan(repo, base):
    code, out = git(repo, "diff", "--name-status", "-M", base, "--")
    if code != 0:
        raise SystemExit(f"git diff failed for base '{base}' (is this a git repo with that ref?)")
    _, untracked = git(repo, "ls-files", "--others", "--exclude-standard")
    rows = [ln.split("\t") for ln in out.splitlines() if ln.strip()]
    rows += [["A", p] for p in untracked.splitlines() if p.strip()]
    findings, skip = [], ignored(repo)
    for row in rows:
        status, path = row[0], row[-1]
        if skip(path):
            continue
        old_path = row[1] if status.startswith("R") else path
        relevant = is_test_file(path) or is_test_file(old_path) or is_ci_file(path) or os.path.basename(path) == "package.json"
        if not relevant:
            continue
        if status.startswith("D"):
            if is_test_file(path):
                findings.append({"file": path, "kind": "test_deleted", "message": "test file deleted"})
            continue
        _, old = git(repo, "show", f"{base}:{old_path}") if not status.startswith("A") else (0, "")
        new = read(os.path.join(repo, path))
        for kind, msg in file_findings(path, old, new):
            findings.append({"file": path, "kind": kind, "message": msg})
    return findings


def snapshot_scan(repo):
    findings, skip = [], ignored(repo)
    for d, dirs, files in os.walk(repo):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, repo)
            if not is_test_file(rel) or skip(rel):
                continue
            m = metrics(read(p))
            for key, label in (("skips", "switched-off tests"), ("only", ".only / focus markers"),
                               ("trivial", "assertions that can never fail"), ("swallow", "swallowed errors")):
                if m[key]:
                    findings.append({"file": rel, "kind": key, "message": f"{m[key]} {label}"})
            if m["asserts"] == 0:
                findings.append({"file": rel, "kind": "no_asserts", "message": "no assert/expect calls found: check this file can fail"})
    return findings


def main(argv):
    as_json = "--json" in argv
    snap = "--snapshot" in argv
    base = argv[argv.index("--base") + 1] if "--base" in argv else "HEAD"
    pos = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--base")]
    repo = os.path.abspath(pos[0] if pos else ".")
    if not snap and git(repo, "rev-parse", "--git-dir")[0] != 0:
        snap = True
    findings = snapshot_scan(repo) if snap else diff_scan(repo, base)
    if as_json:
        print(json.dumps({"mode": "snapshot" if snap else f"diff vs {base}", "findings": findings}, indent=2))
    else:
        title = "Snapshot of the test suite" if snap else f"Test changes vs {base}"
        print(f"test-integrity-guard: {title}")
        if not findings:
            print("  OK: nothing weakened." if not snap else "  OK: no skipped, focused, empty or always-true tests found.")
        for f in findings:
            print(f"  ✖ {f['file']}: {f['message']}")
        if findings and not snap:
            print("\nRead each one: either the code was wrong (fix the code, restore the test), or the test was wrong "
                  "(write down why in the PR so a human approves it).")
    return 1 if findings and not snap else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
