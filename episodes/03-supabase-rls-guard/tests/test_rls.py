#!/usr/bin/env python3
"""Tests for supabase-rls-guard. Run: python3 tests/test_rls.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
from rls_lib import analyze_sql, client_secret_findings, is_client_file, missing_rls, risky_policies  # noqa: E402

# Fake keys built at runtime so no scanner flags this file.
FAKE_SERVICE_JWT = "eyJhbGciOiJIUzI1NiJ9." + "eyJyb2xlIjoic2VydmljZV9yb2xlIn0" + ".abcDEF123"
FAKE_SB_SECRET = "sb_" + "secret_" + "abcdefghijklmnop"


def hook(tool, inp, env=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "guard.py")], input=json.dumps({"tool_name": tool, "tool_input": inp}),
                       capture_output=True, text=True, env=e)
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else "allow"


class SqlTests(unittest.TestCase):
    def test_table_without_rls(self):
        self.assertEqual([t for t, _, _ in missing_rls([("a.sql", "create table todos (id int);")])], ["public.todos"])

    def test_table_with_rls(self):
        sql = 'create table public."Todos"(id int);\nalter table "public"."todos" enable row level security;'
        self.assertEqual(missing_rls([("a.sql", sql)]), [])

    def test_rls_enabled_in_later_migration(self):
        self.assertEqual(missing_rls([("1.sql", "create table if not exists profiles(id uuid);"),
                                      ("2.sql", "alter table profiles enable row level security;")]), [])

    def test_rls_disabled_later(self):
        sql = "create table t(id int); alter table t enable row level security; alter table t disable row level security;"
        self.assertEqual(len(missing_rls([("a.sql", sql)])), 1)

    def test_comment_does_not_count(self):
        sql = "create table t(id int);\n-- alter table t enable row level security;"
        self.assertEqual(len(missing_rls([("a.sql", sql)])), 1)

    def test_policy_true_write_is_risky(self):
        sql = 'create policy "anyone can update" on posts for update using (true);'
        self.assertEqual(len(risky_policies(analyze_sql(sql)["policies"])), 1)

    def test_policy_true_select_is_ok(self):
        sql = 'create policy "public read" on posts for select using (true);'
        self.assertEqual(risky_policies(analyze_sql(sql)["policies"]), [])

    def test_owner_policy_ok(self):
        sql = "create policy own on todos for insert to authenticated with check ((select auth.uid()) = user_id);"
        a = analyze_sql(sql)
        self.assertEqual(a["policies"][0]["cmd"], "insert")
        self.assertEqual(risky_policies(a["policies"]), [])

    def test_view_without_invoker(self):
        a = analyze_sql("create view v_users as select * from users; create view v2 with (security_invoker = true) as select 1;")
        self.assertEqual([(v, inv) for v, inv, _ in a["views"]], [("public.v_users", False), ("public.v2", True)])


class ClientTests(unittest.TestCase):
    def test_client_paths(self):
        self.assertTrue(is_client_file("src/lib/supabase.ts"))
        self.assertTrue(is_client_file("components/Login.tsx"))
        self.assertFalse(is_client_file("supabase/functions/admin/index.ts"))
        self.assertFalse(is_client_file("app/api/admin/route.ts"))

    def test_service_jwt_in_client(self):
        self.assertTrue(client_secret_findings("src/x.ts", f'const k = "{FAKE_SERVICE_JWT}"'))

    def test_sb_secret_in_client(self):
        self.assertTrue(client_secret_findings("src/x.ts", f'const k = "{FAKE_SB_SECRET}"'))

    def test_public_env_secret(self):
        self.assertTrue(client_secret_findings(".env.local", "VITE_SUPABASE_SERVICE_ROLE_KEY=xyz"))
        self.assertFalse(client_secret_findings(".env.local", "VITE_SUPABASE_ANON_KEY=xyz"))

    def test_anon_key_ok(self):
        self.assertFalse(client_secret_findings("src/x.ts", "createClient(import.meta.env.VITE_SUPABASE_URL, import.meta.env.VITE_SUPABASE_ANON_KEY)"))


class HookTests(unittest.TestCase):
    def test_blocks_migration_without_rls(self):
        self.assertEqual(hook("Write", {"file_path": "/p/supabase/migrations/1_init.sql", "content": "create table notes(id int);"}), "deny")

    def test_allows_migration_with_rls(self):
        sql = "create table notes(id int, user_id uuid);\nalter table notes enable row level security;\n" \
              "create policy own on notes for select using ((select auth.uid()) = user_id);"
        self.assertEqual(hook("Write", {"file_path": "/p/supabase/migrations/1_init.sql", "content": sql}), "allow")

    def test_allow_comment(self):
        self.assertEqual(hook("Write", {"file_path": "/p/m.sql", "content": "-- rls-guard: allow\ncreate table countries(id int);"}), "allow")

    def test_blocks_secret_in_client(self):
        self.assertEqual(hook("Write", {"file_path": "/p/src/lib/admin.ts", "content": f'export const k = "{FAKE_SB_SECRET}";'}), "deny")

    def test_edit_applies_to_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.sql")
            with open(p, "w") as f:
                f.write("create table a(id int);\nalter table a enable row level security;\n")
            self.assertEqual(hook("Edit", {"file_path": p, "old_string": "create table a(id int);", "new_string": "create table a(id int);\ncreate table b(id int);"}), "deny")
            self.assertEqual(hook("Edit", {"file_path": p, "old_string": "(id int);\nalter", "new_string": "(id int, n text);\nalter"}), "allow")

    def test_ask_and_off_modes(self):
        inp = {"file_path": "/p/m.sql", "content": "create table x(id int);"}
        self.assertEqual(hook("Write", inp, {"RLS_GUARD_MODE": "ask"}), "ask")
        self.assertEqual(hook("Write", inp, {"RLS_GUARD_MODE": "off"}), "allow")

    def test_ignores_other_tools(self):
        self.assertEqual(hook("Read", {"file_path": "/p/m.sql"}), "allow")


class ScanTests(unittest.TestCase):
    def test_scan_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "supabase", "migrations"))
            with open(os.path.join(d, "supabase", "migrations", "001.sql"), "w") as f:
                f.write("create table orders(id int); create view v as select * from orders;")
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "scan.py"), d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("public.orders", r.stdout)
            with open(os.path.join(d, "supabase", "migrations", "002.sql"), "w") as f:
                f.write("alter table orders enable row level security;")
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "scan.py"), d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
