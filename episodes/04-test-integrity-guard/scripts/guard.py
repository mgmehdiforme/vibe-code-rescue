#!/usr/bin/env python3
"""test-integrity-guard hook (PreToolUse on Write | Edit | MultiEdit | Bash).

Stops Claude Code from turning a red test green by weakening the test instead of fixing the code:
  1. Edits to a test file that remove assertions, swap strict checks for loose ones, switch tests off
     (skip / xit / Ignore), add `.only`, add an assertion that can never fail, or swallow errors.
  2. Shell commands that delete test files or make a failing run look green
     (`|| true`, `--passWithNoTests`, `-k "not ..."`, snapshot overwrite).
  3. CI files that get `continue-on-error: true` / `allow_failure: true`.

Settings: TEST_GUARD_MODE = ask (default: you approve each one) | block | off
Allowlist: .testguardignore in the project root (one glob per line).
"""
import fnmatch
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tig_lib import command_findings, file_findings  # noqa: E402

MODE = os.environ.get("TEST_GUARD_MODE", "ask").lower()
ADVICE = (" If the code is wrong, fix the code, not the test. If the test itself is wrong, stop and tell the user "
          "which assertion changes and why, so a human approves it. (test-integrity-guard; TEST_GUARD_MODE=block|ask|off)")


def project_root():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def allowlist():
    try:
        with open(os.path.join(project_root(), ".testguardignore"), encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []


def read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def before_after(tool, inp):
    """(old, new) content of the file around this tool call (best effort)."""
    old = read(inp.get("file_path", ""))
    if tool == "Write":
        return old, inp.get("content", "")
    text = old
    edits = inp.get("edits") if tool == "MultiEdit" else [inp]
    for e in edits or []:
        o, n = e.get("old_string", ""), e.get("new_string", "")
        if o and o in text:
            text = text.replace(o, n) if e.get("replace_all") else text.replace(o, n, 1)
        else:
            # File unknown to us: judge the edit itself (old_string -> new_string).
            return "\n".join(x.get("old_string", "") for x in edits), "\n".join(x.get("new_string", "") for x in edits)
    return old, text


def decide(reasons, where):
    if not reasons:
        return
    msg = f"{where}: " + "; ".join(reasons) + "." + ADVICE
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny" if MODE == "block" else "ask",
        "permissionDecisionReason": msg,
    }}))


def main():
    if MODE == "off":
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    tool = event.get("tool_name", "")
    inp = event.get("tool_input", {}) or {}
    if tool == "Bash":
        found = command_findings(inp.get("command", ""))
        decide([m for _, m in found], "This command weakens your test run")
        return
    path = inp.get("file_path", "")
    if tool not in ("Write", "Edit", "MultiEdit") or not path:
        return
    rel = os.path.relpath(path, project_root())
    if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(path, g) for g in allowlist()):
        return
    old, new = before_after(tool, inp)
    found = file_findings(path, old, new)
    decide([m for _, m in found], f"This change makes {rel} check less")


if __name__ == "__main__":
    main()
