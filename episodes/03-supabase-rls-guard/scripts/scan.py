#!/usr/bin/env python3
"""supabase-rls-guard project scanner.

Usage: python3 scan.py [project_dir] [--json]

Reads the project's SQL (supabase/migrations, *.sql) and front-end code and
reports the 7-point RLS checklist. Never reads .env values aloud: findings in
.env files name the variable, not the value. Exit code 1 when a critical
finding exists, so it can run in CI.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rls_lib import analyze_sql, client_secret_findings, is_client_file, missing_rls, risky_policies  # noqa: E402

SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", ".nuxt", ".svelte-kit", ".vercel", ".output", "coverage", "venv", ".venv", "__pycache__"}


def walk(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            yield os.path.join(d, f)


def read(p):
    try:
        if os.path.getsize(p) > 2_000_000:
            return ""
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def scan(root):
    sql, client = [], []
    for p in walk(root):
        rel = os.path.relpath(p, root)
        if p.endswith(".sql") and "rls-guard: allow" not in read(p).lower():
            sql.append((rel, read(p)))
        elif is_client_file(rel):
            client.append((rel, p))
    sql.sort()  # migrations are timestamp-prefixed, so this is apply order

    policies, views, grants, definers = [], [], [], []
    for rel, text in sql:
        a = analyze_sql(text)
        policies += [dict(p, file=rel) for p in a["policies"]]
        views += [(v, inv, rel, ln) for v, inv, ln in a["views"]]
        grants += [(t, pr, rel, ln) for t, pr, ln in a["anon_grants"]]
        definers += [(f, rel, ln) for f, ln in a["definer_fns"]]
    tables = {t for _, text in sql for t, _ in analyze_sql(text)["created"]}
    with_policy = {p["table"] for p in policies}

    secrets = []
    for rel, p in client:
        secrets += [(rel, ln, what) for ln, what in client_secret_findings(rel, read(p))]

    checks = [
        ("1. RLS enabled on every table", "critical",
         [f"{f}:{ln}  {t} has no `enable row level security`" for t, f, ln in missing_rls(sql)]),
        ("2. A policy for each allowed operation", "warn",
         [f"{t} has RLS but no policy (nobody but service_role can use it; fine only if that is intended)" for t in sorted(tables - with_policy)
          if t not in {x for x, _, _ in missing_rls(sql)}]),
        ("3. No policy lets anyone write any row", "critical",
         [f"{p['file']}:{p['line']}  policy \"{p['name']}\" on {p['table']}: {p['cmd'].upper()} with `true`" for p in risky_policies(policies)]),
        ("4. anon has no grants on login-only tables", "warn",
         [f"{f}:{ln}  grant {pr.strip()} on {t} to anon" for t, pr, f, ln in grants]),
        ("5. Secret key never in browser code", "critical",
         [f"{f}:{ln}  {what}" for f, ln, what in secrets]),
        ("6. Views and SECURITY DEFINER functions reviewed", "warn",
         [f"{f}:{ln}  view {v} without security_invoker=true (views bypass RLS by default)" for v, inv, f, ln in views if not inv]
         + [f"{f}:{ln}  function {fn} is SECURITY DEFINER (runs as owner, skips RLS): check its access logic" for fn, f, ln in definers]),
        ("7. Re-checked against the live database", "manual",
         ["Run sql/rls-check.sql in the Supabase SQL editor; migrations do not show tables created in the dashboard or by hand."]),
    ]
    return {"sql_files": len(sql), "client_files": len(client), "checks": checks}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = os.path.abspath(args[0] if args else ".")
    res = scan(root)
    if "--json" in sys.argv:
        print(json.dumps({"root": root, **res}, indent=2))
    else:
        print(f"supabase-rls-guard scan: {root}")
        print(f"  {res['sql_files']} SQL files, {res['client_files']} browser-side files\n")
        for title, level, items in res["checks"]:
            status = "MANUAL" if level == "manual" else ("OK" if not items else ("FAIL" if level == "critical" else "CHECK"))
            print(f"[{status:6}] {title}")
            for it in items[:25]:
                print(f"           - {it}")
            if len(items) > 25:
                print(f"           … and {len(items) - 25} more")
        if res["sql_files"] == 0:
            print("\nNo SQL migrations found. If the tables were made in the dashboard, run sql/rls-check.sql against the database.")
    sys.exit(1 if any(lv == "critical" and it for _, lv, it in res["checks"]) else 0)


if __name__ == "__main__":
    main()
