#!/usr/bin/env python3
"""supabase-rls-guard hook (PreToolUse on Write | Edit | MultiEdit).

Stops Claude Code from writing the mistakes behind the Lovable/Supabase leaks:
  1. A SQL file that creates a table without enabling Row Level Security.
  2. A policy that lets everyone insert/update/delete with `using (true)` / `with check (true)`.
  3. A service_role / sb_secret_ key (or a public-prefixed secret env var) in browser code.

Settings: RLS_GUARD_MODE = block (default) | ask | off
Allowlist: .rlsguardignore in the project root (one glob per line) or a
`-- rls-guard: allow` comment in the SQL for an intentional public table.
"""
import fnmatch
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rls_lib import analyze_sql, client_secret_findings, is_client_file, missing_rls, risky_policies  # noqa: E402

MODE = os.environ.get("RLS_GUARD_MODE", "block").lower()
FOOTER = " (supabase-rls-guard; add `-- rls-guard: allow` for an intentional public table, or set RLS_GUARD_MODE=ask)"


def allowlist():
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        with open(os.path.join(root, ".rlsguardignore"), encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []


def resulting_text(tool, inp):
    """The file content after this tool call (best effort)."""
    path = inp.get("file_path", "")
    if tool == "Write":
        return inp.get("content", "")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    edits = inp.get("edits") if tool == "MultiEdit" else [inp]
    for e in edits or []:
        old, new = e.get("old_string", ""), e.get("new_string", "")
        if old and old in text:
            text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
        else:
            text += "\n" + new
    return text


def problems(path, text):
    out = []
    if path.lower().endswith(".sql"):
        if "rls-guard: allow" in text.lower():
            return out
        for table, _, line in missing_rls([(path, text)]):
            out.append(f"`{table}` is created (line {line}) without `alter table {table} enable row level security;`. "
                       "Tables created with SQL do not get RLS by default, so the public anon key can read and write every row. "
                       "Enable RLS in the same migration and add a policy per operation, e.g. "
                       "`using ((select auth.uid()) = user_id)` for select/delete and `with check` for insert/update.")
        for p in risky_policies(analyze_sql(text)["policies"]):
            out.append(f"Policy \"{p['name']}\" on `{p['table']}` (line {p['line']}) allows {p['cmd'].upper()} for any row with `true`. "
                       "Restrict it to the row owner, e.g. `with check ((select auth.uid()) = user_id)`.")
    elif is_client_file(path):
        for line, what in client_secret_findings(path, text):
            out.append(f"{path}:{line} has {what}. The service_role / secret key bypasses RLS and must stay on the server "
                       "(edge function or backend). The browser should only ever get the anon / publishable key.")
    return out


def main():
    if MODE == "off":
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    tool = event.get("tool_name", "")
    inp = event.get("tool_input", {}) or {}
    path = inp.get("file_path", "")
    if tool not in ("Write", "Edit", "MultiEdit") or not path:
        return
    rel = os.path.relpath(path, os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(path, g) for g in allowlist()):
        return
    found = problems(path, resulting_text(tool, inp))
    if not found:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask" if MODE == "ask" else "deny",
        "permissionDecisionReason": " ".join(found) + FOOTER,
    }}))


if __name__ == "__main__":
    main()
