"""Shared checks for supabase-rls-guard (zero dependencies, Python 3.8+).

Static analysis of SQL and front-end code for the mistakes behind the
Lovable/Supabase data exposures: tables without Row Level Security, policies
that allow everyone to write, anon grants, views that skip RLS, and the
service_role / secret key shipped to the browser.
"""
import base64
import json
import os
import re

IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)'
QNAME = rf'(?:{IDENT}\s*\.\s*)?{IDENT}'

CREATE_TABLE = re.compile(
    rf'\bcreate\s+(?:(?:global\s+|local\s+)?(?:temp|temporary|unlogged)\s+)?table\s+(?:if\s+not\s+exists\s+)?({QNAME})',
    re.I)
ENABLE_RLS = re.compile(rf'\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({QNAME})\s+enable\s+row\s+level\s+security', re.I)
DISABLE_RLS = re.compile(rf'\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({QNAME})\s+disable\s+row\s+level\s+security', re.I)
CREATE_POLICY = re.compile(
    rf'\bcreate\s+policy\s+("[^"]+"|\S+)\s+on\s+({QNAME})(.*?);', re.I | re.S)
CREATE_VIEW = re.compile(
    rf'\bcreate\s+(?:or\s+replace\s+)?(?:materialized\s+)?view\s+(?:if\s+not\s+exists\s+)?({QNAME})(.*?)\bas\b', re.I | re.S)
GRANT_ANON = re.compile(
    rf'\bgrant\s+([\w\s,]+?)\s+on\s+(?:table\s+)?({QNAME}|all\s+tables\s+in\s+schema\s+{IDENT})\s+to\s+([\w\s,]*\banon\b[\w\s,]*)', re.I)
SECURITY_DEFINER = re.compile(rf'\bcreate\s+(?:or\s+replace\s+)?function\s+({QNAME}).*?\bsecurity\s+definer\b', re.I | re.S)

# Secret Supabase keys that must never reach a browser.
JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{5,}\.(eyJ[A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+')


def is_service_jwt(line):
    """True when the line holds a JWT whose payload says role = service_role."""
    for m in JWT.finditer(line):
        part = m.group(1)
        try:
            payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("role") == "service_role":
            return True
    return False
SB_SECRET = re.compile(r'\bsb_secret_[A-Za-z0-9_-]{10,}')
PUBLIC_SECRET_VAR = re.compile(
    r'\b(?:VITE|NEXT_PUBLIC|EXPO_PUBLIC|REACT_APP|PUBLIC|NUXT_PUBLIC|GATSBY)_\w*(?:SERVICE_ROLE|SERVICE_KEY|SECRET_KEY|SUPABASE_SECRET)\w*', re.I)
SERVICE_VAR_IN_CLIENT = re.compile(r'\b\w*SUPABASE_SERVICE_ROLE(?:_KEY)?\b|\bSUPABASE_SECRET_KEY\b', re.I)

CLIENT_DIRS = ("src/", "app/", "pages/", "components/", "public/", "client/", "frontend/", "web/", "lib/", "hooks/")
CLIENT_EXT = (".tsx", ".jsx", ".vue", ".svelte", ".astro", ".html")
SERVER_HINTS = ("supabase/functions/", "/api/", "server/", "backend/", "/server.", ".server.", "middleware.", "route.ts", "route.js",
                "edge-functions/", "netlify/functions/", "functions/")
WRITE_CMDS = ("insert", "update", "delete", "all")


def norm(name):
    """public.Foo / "public"."foo" / foo  ->  public.foo"""
    parts = [p.strip().strip('"') for p in re.split(r'\s*\.\s*', name.strip())]
    if len(parts) == 1:
        parts = ["public"] + parts
    return ".".join(parts).lower()


def strip_comments(sql):
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.S)
    return re.sub(r'--[^\n]*', ' ', sql)


def analyze_sql(sql):
    """Return a dict describing tables, RLS and policies found in a SQL text."""
    sql = strip_comments(sql)
    out = {"created": [], "rls_on": set(), "rls_off": set(), "policies": [], "views": [], "anon_grants": [], "definer_fns": []}
    for m in CREATE_TABLE.finditer(sql):
        out["created"].append((norm(m.group(1)), sql.count("\n", 0, m.start()) + 1))
    for m in ENABLE_RLS.finditer(sql):
        out["rls_on"].add(norm(m.group(1)))
    for m in DISABLE_RLS.finditer(sql):
        out["rls_off"].add(norm(m.group(1)))
    for m in CREATE_POLICY.finditer(sql):
        body = m.group(3)
        cmd = re.search(r'\bfor\s+(select|insert|update|delete|all)\b', body, re.I)
        roles = re.search(r'\bto\s+([\w\s,]+?)(?:\s+using|\s+with\s+check|$)', body, re.I)
        out["policies"].append({
            "name": m.group(1).strip('"'),
            "table": norm(m.group(2)),
            "cmd": (cmd.group(1).lower() if cmd else "all"),
            "roles": [r.strip().lower() for r in roles.group(1).split(",")] if roles else ["public"],
            "always_true": bool(re.search(r'\b(?:using|with\s+check)\s*\(\s*true\s*\)', body, re.I)),
            "line": sql.count("\n", 0, m.start()) + 1,
        })
    for m in CREATE_VIEW.finditer(sql):
        opts = m.group(2) or ""
        out["views"].append((norm(m.group(1)), bool(re.search(r'security_invoker\s*=\s*(?:true|on)', opts, re.I)),
                             sql.count("\n", 0, m.start()) + 1))
    for m in GRANT_ANON.finditer(sql):
        privs = m.group(1).lower()
        out["anon_grants"].append((m.group(2), privs, sql.count("\n", 0, m.start()) + 1))
    for m in SECURITY_DEFINER.finditer(sql):
        out["definer_fns"].append((norm(m.group(1)), sql.count("\n", 0, m.start()) + 1))
    return out


def missing_rls(sql_texts):
    """Tables created anywhere in sql_texts with no 'enable row level security' anywhere (or disabled later)."""
    created, on, off = [], set(), set()
    for src, text in sql_texts:
        a = analyze_sql(text)
        created += [(t, src, line) for t, line in a["created"]]
        on |= a["rls_on"]
        off |= a["rls_off"]
    return [(t, src, line) for t, src, line in created
            if not t.startswith(("pg_temp", "temp.")) and (t not in on or t in off)]


def risky_policies(policies):
    """Policies that let anyone (or every signed-in user) write any row."""
    bad = []
    for p in policies:
        if p["always_true"] and p["cmd"] in WRITE_CMDS:
            bad.append(p)
    return bad


def is_client_file(path):
    p = path.replace("\\", "/").lower()
    if any(h in p for h in SERVER_HINTS):
        return False
    base = os.path.basename(p)
    if base.startswith(".env"):
        return True  # checked for public-prefixed vars only
    return p.endswith(CLIENT_EXT) or (p.endswith((".ts", ".js", ".mjs")) and any(("/" + d) in ("/" + p) for d in CLIENT_DIRS))


def client_secret_findings(path, text):
    """Secret Supabase keys or secret-key variables that would end up in a browser bundle."""
    found = []
    base = os.path.basename(path).lower()
    for i, line in enumerate(text.splitlines(), 1):
        if base.startswith(".env"):
            if PUBLIC_SECRET_VAR.search(line):
                found.append((i, "a secret key in a browser-exposed env variable (" + PUBLIC_SECRET_VAR.search(line).group(0) + ")"))
            continue
        if is_service_jwt(line):
            found.append((i, "a service_role JWT in client code"))
        elif SB_SECRET.search(line):
            found.append((i, "an sb_secret_ key in client code"))
        elif PUBLIC_SECRET_VAR.search(line):
            found.append((i, "a secret key read from a browser-exposed env variable"))
        elif SERVICE_VAR_IN_CLIENT.search(line):
            found.append((i, "the service role key referenced in client code"))
    return found


def mask(s):
    return s[:4] + "…" if len(s) > 4 else s
