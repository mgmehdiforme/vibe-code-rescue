-- supabase-rls-guard: live database check
-- Paste into the Supabase SQL editor (Dashboard > SQL Editor) and run.
-- Read-only. Every query should return ZERO rows on a locked-down app,
-- except where a table is meant to be public (then make that a conscious choice).

-- 1. Public tables with Row Level Security OFF (anyone with the anon key can use them)
select 'rls_off' as finding, schemaname || '.' || tablename as object
from pg_tables
where schemaname = 'public' and not rowsecurity

union all
-- 2. Tables with RLS ON but no policy (locked for everyone except service_role: intended?)
select 'rls_on_no_policy', n.nspname || '.' || c.relname
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r' and c.relrowsecurity
  and not exists (select 1 from pg_policies p where p.schemaname = n.nspname and p.tablename = c.relname)

union all
-- 3. Policies that let a write through for every row
select 'write_policy_true', schemaname || '.' || tablename || ' / ' || policyname || ' (' || cmd || ')'
from pg_policies
where schemaname = 'public'
  and cmd in ('INSERT', 'UPDATE', 'DELETE', 'ALL')
  and (coalesce(qual, '') = 'true' or coalesce(with_check, '') = 'true')

union all
-- 4. Write grants the anon role still has (adding policies does not remove grants)
select 'anon_write_grant', table_schema || '.' || table_name || ' / ' || string_agg(privilege_type, ',')
from information_schema.role_table_grants
where grantee = 'anon' and table_schema = 'public'
  and privilege_type in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
group by table_schema, table_name

union all
-- 5. Views in public without security_invoker (they run as the owner and skip RLS)
select 'view_skips_rls', n.nspname || '.' || c.relname
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind in ('v', 'm')
  and not coalesce(c.reloptions::text ilike '%security_invoker=true%' or c.reloptions::text ilike '%security_invoker=on%', false)

union all
-- 6. SECURITY DEFINER functions callable through the API (check their access logic by hand)
select 'security_definer_fn', n.nspname || '.' || p.proname
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.prosecdef

order by 1, 2;
