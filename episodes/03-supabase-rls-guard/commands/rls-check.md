---
description: Check this Supabase project against the 7-point Row Level Security checklist and propose fixes
argument-hint: "[project folder, default: current]"
allowed-tools: Bash(python3:*), Read, Grep, Glob
---

Use the `supabase-rls-audit` skill.

1. Run: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" ${ARGUMENTS:-.}`
2. Verify each finding by reading the flagged migration or source file. Never print key values; name the file, line and key type.
3. Show the user the live check to run in the Supabase SQL editor: the contents of `${CLAUDE_PLUGIN_ROOT}/sql/rls-check.sql`. Explain that every returned row is a finding.
4. Report the 7 checks as a table (status + evidence), then the fixes in priority order, with the exact SQL for a new migration.
5. Ask which fixes to apply. Do not change code or the database before the user says which.
