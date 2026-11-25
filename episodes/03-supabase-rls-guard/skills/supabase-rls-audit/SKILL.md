---
name: supabase-rls-audit
description: Audit and fix Supabase Row Level Security in an AI-built app (Lovable, Bolt, Cursor, Claude Code). Use when the user asks whether their Supabase/Lovable database is exposed, wants to add or review RLS policies, sees "new row violates row-level security policy" or "saved but nothing saved", is about to launch a Supabase app, worries about the anon key, service_role key or sb_secret_ key, or asks how to lock down tables, views or SECURITY DEFINER functions.
---

# Supabase Row Level Security audit

A Supabase app talks to Postgres straight from the browser. Every visitor gets the project URL and the anon (publishable) key. That key is public by design. The only thing between a stranger and your rows is **grants** (may this role run this operation?) plus **Row Level Security policies** (which rows?). A table in an exposed schema without RLS is readable and writable by any role with a grant on it.

Real-world basis (cite these, do not invent more):
- Crissy Field scan reported by Cybernews (Nov 2026): of 159,041 Lovable projects, 34,545 used Supabase and 24,664 returned database records with no login; emails were readable on 7,403 sites; one database held 1,000+ passport records; a forum leaked 200,000+ chat messages. https://cybernews.com/security/lovable-ai-apps-expose-personal-information/
- UpGuard (Sep 2026): 16,326 Supabase databases with tables readable from outside, over half with signs of personal data. https://www.upguard.com/labs/research/everything-everywhere-systemic-data-exposure-in-supabase-apps
- The Register (Feb 2026): a Lovable-hosted app exposed 18,000+ users because an access function was inverted (blocked signed-in users, allowed anonymous ones). https://www.theregister.com/2026/02/27/lovable_app_vulnerabilities

Why AI-built apps hit this: tables made in the Supabase Table Editor get RLS on by default; tables created with SQL (how coding agents create them) do not. Supabase stopped auto-exposing new tables (new projects since 30 May 2026, new tables in existing projects since 30 Oct 2026), but tables created earlier keep their old grants.

## The 7-point checklist
1. Every public table has RLS enabled.
2. Every table has a policy for each operation the app allows (select, insert, update, delete).
3. `anon` has no grants on tables that need a login. Policies do not remove grants.
4. The `service_role` / `sb_secret_` key is never in the browser bundle or a public env var (`VITE_`, `NEXT_PUBLIC_`, `EXPO_PUBLIC_`…).
5. Tested logged out, and as user A trying to read and write user B's rows.
6. A human read every policy, view and SECURITY DEFINER function the AI wrote, looking for inverted or `true` logic.
7. The check is re-run after every change that creates or alters a table.

## How to audit
1. Static: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" <project>` (migrations + client code).
2. Live: ask the user to paste `${CLAUDE_PLUGIN_ROOT}/sql/rls-check.sql` into the Supabase SQL editor and share the result. Every row is a finding. Never ask for the service_role key or a database password.
3. Logged-out test (user's own app only): open the app in a private window, DevTools > Network, find a `.supabase.co/rest/v1/` request, replay it without the `Authorization: Bearer <user token>` header. Rows that come back are visible to strangers.

## Fix recipes
Owner-only table:
```sql
alter table public.todos enable row level security;
create policy "todos: owner reads"   on public.todos for select to authenticated using ((select auth.uid()) = user_id);
create policy "todos: owner inserts" on public.todos for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "todos: owner updates" on public.todos for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "todos: owner deletes" on public.todos for delete to authenticated using ((select auth.uid()) = user_id);
```
- `using` filters existing rows (select, update, delete). `with check` validates new rows (insert, update). An insert policy only takes `with check`.
- Wrap `auth.uid()` in `(select ...)` so Postgres evaluates it once per query, and index the owner column.
- Public read, private write: `for select to anon, authenticated using (true)` is fine for data meant to be public; never use `true` for insert/update/delete.
- Remove leftover grants: `revoke insert, update, delete, truncate on public.todos from anon;`
- Views: `create view ... with (security_invoker = true) as ...` (Postgres 15+), or move them out of `public`.
- SECURITY DEFINER functions: check the caller with `auth.uid()` inside, set `search_path = ''`, and `revoke execute ... from anon` if only signed-in users need them.
- Server work that needs to bypass RLS goes in an Edge Function or backend with the secret key from server env, never the browser.

## "Saved" but nothing saved
After RLS is on, inserts or updates that return no error but change no rows usually mean a missing `with check` (insert) or `using` (update) policy. Add the matching policy instead of turning RLS off.

## Output format
Report the 7 checks as a table (OK / FAIL / CHECK / MANUAL, with file:line or SQL evidence), then fixes in priority order: (1) secret key out of the browser and rotated, (2) RLS on every table, (3) write policies, (4) grants, views and functions. Ask before changing code.
