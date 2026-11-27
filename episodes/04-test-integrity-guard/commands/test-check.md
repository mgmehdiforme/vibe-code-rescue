---
description: Check whether the current changes made your tests check less (removed or loosened assertions, skipped tests, silent test runs)
argument-hint: "[base ref, default: HEAD; e.g. origin/main]"
allowed-tools: Bash(python3:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Read, Grep, Glob
---

Use the `test-integrity-audit` skill.

1. Run: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" . --base ${ARGUMENTS:-HEAD}` and then `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" . --snapshot`.
2. For every finding, open the diff of that file (`git diff ${ARGUMENTS:-HEAD} -- <file>`) and show the exact assertion before and after.
3. For each one, decide with evidence: (a) the code was wrong and the test was weakened to hide it → restore the test and fix the code; (b) the test was really wrong → say what the correct expected value is and why.
4. Report as a table: file, what got weaker, before → after, verdict (restore / justified / needs a human), then the 7-point checklist status.
5. Ask the user which restores to apply. Do not edit tests or code before they answer.
