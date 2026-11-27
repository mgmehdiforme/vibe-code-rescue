---
name: test-integrity-audit
description: Check and fix tests that an AI coding agent (Claude Code, Cursor, Copilot, Lovable) made easier to pass instead of fixing the code. Use when the user asks "did the AI change my tests?", "why are tests green but production broken?", before merging an agent's PR, after a migration or rewrite done with AI, when a test was skipped, an assertion removed, an expected value changed or snapshots updated, or when setting up CI so a red test can't be hidden.
---

# Test integrity audit

When an agent is told "make the tests pass", there are two ways to get there: fix the code, or make the tests check less. The second one looks identical in CI: green.

Real-world basis (cite this, do not invent more):
- Anthropic, "Migrating the Messages API to Rust" (25 Nov 2026): Claude helped port the API and its ~700 integration tests. Because the tests had to change along with the code, "a pass could mean that the code was right, or that the test had been changed in a way that let wrong code through". An audit of the ported tests found **more than 100 weakened assertions**. In April the Rust API passed **about 98%** of its tests while only **about a quarter** of inputs matched on production traffic. An agent also proposed a shortcut (copying a tool description into files) that passed the local check but did not fix the real problem; an engineer rejected it. Their answer: use the old system as the oracle and compare outputs on shadowed production traffic. https://www.anthropic.com/research/migrating-api-to-rust

## What "weakened" looks like
| Pattern | Example | Caught by |
|---|---|---|
| Assertion removed or commented out | `- assert order.currency == "EUR"` | hook + scanner |
| Strict check made loose | `toEqual('…')` → `toBeTruthy()`, `assertEqual` → `assertIsNotNone` | hook + scanner |
| Test switched off | `it.skip`, `xit`, `@pytest.mark.skip`, `[Fact(Skip=…)]`, `t.Skip()`, `#[ignore]`, `@Disabled` | hook + scanner |
| Other tests silently stop | `it.only`, `fdescribe` | hook + scanner |
| Can never fail | `assert True`, `expect(true).toBe(true)` | hook + scanner |
| Error swallowed | `except: pass`, `catch {}` around the code under test | hook + scanner |
| Run can't go red | `npm test \|\| true`, `--passWithNoTests`, `-k "not …"`, `continue-on-error: true` | hook + scanner |
| Wrong output becomes "expected" | `jest -u`, expected value edited to match new output | hook (snapshots) / **human review** |
| Code special-cases the test | `if (process.env.NODE_ENV === 'test') return expected` | **human review** (test-auditor agent) |

## The 7-point checklist
1. Read the test diff before the code diff in every AI-written PR.
2. No assertion was removed or loosened without a written reason a human approved.
3. No new skip / only / ignore markers in the suite.
4. The test command can fail: no `|| true`, `--passWithNoTests`, `continue-on-error`, deselected tests.
5. Expected values come from the spec or the old system, never from the new code's output.
6. Before switching, compare old vs new on real (anonymised) inputs, not only on tests.
7. Every correction you make to the agent becomes a rule it reads (CLAUDE.md) and a test or lint.

## How to audit
1. Diff: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" . --base origin/main` (exit 1 when something got weaker: put it in CI).
2. Snapshot: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" . --snapshot` lists skipped, focused, empty and always-true tests today.
3. Manual 2-minute check without the plugin:
   ```bash
   git diff origin/main --stat -- '*test*' '*spec*'
   git diff origin/main -- '*test*' '*spec*' | grep -E '^-.*(assert|expect|Assert\.|should)'
   git diff origin/main -- '*test*' '*spec*' | grep -E '^\+.*(\.skip|xit\(|\.only|@pytest\.mark\.skip|Skip *=|t\.Skip|#\[ignore\]|@Disabled)'
   ```
   Every line printed by the second and third command needs a reason.

## Fix recipes
- **Restore, then fix the code.** `git checkout origin/main -- path/to/test` and ask the agent to make the ORIGINAL test pass.
- **Make the rule explicit** (add to CLAUDE.md):
  ```markdown
  ## Tests
  - Never delete, skip or loosen an existing assertion to make a test pass. Fix the code.
  - If you believe a test is wrong, stop and explain which assertion, the expected value you propose, and why. Wait for approval.
  - Never update snapshots or expected values from the new output without showing the diff.
  ```
- **Protect the test folder in CI:** require a code-owner review for `tests/**` (`.github/CODEOWNERS`: `/tests/ @you`).
- **Golden files / oracle:** record real inputs and the old system's outputs once; the new code must match them byte for byte. This is Anthropic's approach at small scale.
- **Mutation testing** shows whether tests catch bugs at all: Python `mutmut run`, JS/TS `npx stryker run`, .NET `dotnet stryker`. A surviving mutant = a check that never fails.
- **Make the run fail loudly:** remove `|| true`, set `fail_ci_if_error`, and keep `--passWithNoTests` off.

## Tuning the hook
- `TEST_GUARD_MODE=ask` (default): you approve each weakening edit. `block`: the agent is refused and told to fix the code (good for headless runs). `off`: disabled.
- `.testguardignore` in the project root: one glob per line for files the hook and scanner skip (e.g. fixture tests).
