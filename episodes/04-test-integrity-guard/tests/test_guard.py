#!/usr/bin/env python3
"""Tests for test-integrity-guard. Run: python3 tests/test_guard.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
from tig_lib import command_findings, compare, file_findings, is_test_file, metrics  # noqa: E402


def kinds(findings):
    return sorted(k for k, _ in findings)


def hook(tool, inp, env=None, cwd=None):
    e = dict(os.environ, **(env or {}))
    e.pop("TEST_GUARD_MODE", None) if not (env and "TEST_GUARD_MODE" in env) else None
    if cwd:
        e["CLAUDE_PROJECT_DIR"] = cwd
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "guard.py")], input=json.dumps({"tool_name": tool, "tool_input": inp}),
                       capture_output=True, text=True, env=e)
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else "allow"


PY_OLD = """def test_total():
    order = make_order()
    assert order.total == 42
    assert order.currency == "EUR"
    self.assertEqual(order.items, 3)
"""

JS_OLD = """test('escapes emoji in tool calls', () => {
  const out = render(call);
  expect(out).toEqual('{"text":"\\\\ud83d\\\\ude00"}');
  expect(out.length).toBe(24);
});
"""


class TestFileDetection(unittest.TestCase):
    def test_names(self):
        for p in ["tests/test_api.py", "api_test.py", "src/a.test.ts", "web/__tests__/x.js", "pkg/x_test.go",
                  "Api.Tests/OrderTests.cs", "src/test/java/FooTest.java", "spec/models/user_spec.rb", "b.spec.tsx"]:
            self.assertTrue(is_test_file(p), p)

    def test_not_tests(self):
        for p in ["src/api.py", "src/attestation.ts", "README.md", "src/contest.go", "latest.cs"]:
            self.assertFalse(is_test_file(p), p)


class CompareTests(unittest.TestCase):
    def test_assertion_removed(self):
        new = PY_OLD.replace('    assert order.currency == "EUR"\n', "")
        self.assertIn("assert_removed", kinds(compare(PY_OLD, new)))

    def test_commented_out_assertion_counts_as_removed(self):
        new = PY_OLD.replace('    assert order.currency', '    # assert order.currency')
        self.assertIn("assert_removed", kinds(compare(PY_OLD, new)))

    def test_loosened_matcher(self):
        new = JS_OLD.replace("expect(out).toEqual('{\"text\":\"\\\\ud83d\\\\ude00\"}');", "expect(out).toBeTruthy();")
        self.assertIn("assert_loosened", kinds(compare(JS_OLD, new)))

    def test_python_loosened(self):
        new = PY_OLD.replace("self.assertEqual(order.items, 3)", "self.assertIsNotNone(order.items)")
        self.assertIn("assert_loosened", kinds(compare(PY_OLD, new)))

    def test_skips(self):
        cases = [
            (JS_OLD, JS_OLD.replace("test(", "test.skip(", 1)),
            (JS_OLD, JS_OLD.replace("test(", "xit(", 1)),
            (PY_OLD, "import pytest\n@pytest.mark.skip(reason='flaky')\n" + PY_OLD),
            ("[Fact]\npublic void Total() { Assert.Equal(42, t); }", '[Fact(Skip = "later")]\npublic void Total() { Assert.Equal(42, t); }'),
            ("func TestTotal(t *testing.T) {\n if got != 42 { t.Errorf(\"x\") }\n}", "func TestTotal(t *testing.T) {\n t.Skip(\"later\")\n if got != 42 { t.Errorf(\"x\") }\n}"),
            ("#[test]\nfn total() { assert_eq!(t, 42); }", "#[test]\n#[ignore]\nfn total() { assert_eq!(t, 42); }"),
            ("@Test\nvoid total() { Assertions.assertEquals(42, t); }", "@Test\n@Disabled\nvoid total() { Assertions.assertEquals(42, t); }"),
        ]
        for old, new in cases:
            self.assertIn("skip_added", kinds(compare(old, new)), new)

    def test_only(self):
        self.assertIn("only_added", kinds(compare(JS_OLD, JS_OLD.replace("test(", "test.only(", 1))))

    def test_trivial(self):
        new = PY_OLD.replace("assert order.total == 42", "assert True")
        self.assertIn("trivial_assert", kinds(compare(PY_OLD, new)))
        self.assertIn("trivial_assert", kinds(compare(JS_OLD, JS_OLD.replace("expect(out.length).toBe(24)", "expect(true).toBe(true)"))))

    def test_swallow(self):
        new = PY_OLD.replace("    order = make_order()\n", "    try:\n        order = make_order()\n    except Exception:\n        pass\n")
        self.assertIn("swallowed_error", kinds(compare(PY_OLD, new)))

    def test_refactor_keeping_checks_is_fine(self):
        new = PY_OLD.replace("order = make_order()", "order = make_order(currency='EUR')")
        self.assertEqual(compare(PY_OLD, new), [])

    def test_adding_tests_is_fine(self):
        new = PY_OLD + "\ndef test_empty():\n    assert make_order(items=[]).total == 0\n"
        self.assertEqual(compare(PY_OLD, new), [])

    def test_new_file(self):
        self.assertEqual(compare("", PY_OLD), [])

    def test_counts(self):
        m = metrics(PY_OLD)
        self.assertEqual(m["asserts"], 3)


class CommandTests(unittest.TestCase):
    def test_or_true(self):
        self.assertIn("test_cmd_or_true", kinds(command_findings("npm test || true")))
        self.assertIn("test_cmd_or_true", kinds(command_findings("pytest -q tests/ || exit 0")))

    def test_pass_with_no_tests(self):
        self.assertIn("pass_with_no_tests", kinds(command_findings("npx jest --passWithNoTests")))

    def test_deselect(self):
        self.assertIn("deselect", kinds(command_findings("pytest -k 'not test_emoji'")))

    def test_snapshot_update(self):
        self.assertIn("update_snapshots", kinds(command_findings("npx jest -u")))

    def test_rm_tests(self):
        self.assertIn("test_deleted", kinds(command_findings("rm tests/test_api.py")))
        self.assertIn("test_deleted", kinds(command_findings("git rm -f src/order.test.ts")))
        self.assertIn("test_deleted", kinds(command_findings("rm -rf __tests__")))

    def test_normal_commands(self):
        for c in ["pytest -q", "npm test", "rm -rf dist build", "git rm src/old_api.py", "dotnet test --no-build", "go test ./..."]:
            self.assertEqual(command_findings(c), [], c)

    def test_ci(self):
        old = "steps:\n  - run: npm test\n"
        new = "steps:\n  - run: npm test\n    continue-on-error: true\n"
        self.assertIn("ci_continue_on_error", kinds(file_findings(".github/workflows/test.yml", old, new)))
        self.assertEqual(file_findings(".github/workflows/test.yml", old, old + "  - run: npm run lint\n"), [])

    def test_package_json(self):
        old = '{"scripts":{"test":"jest"}}'
        new = '{"scripts":{"test":"jest --passWithNoTests"}}'
        self.assertIn("pass_with_no_tests", kinds(file_findings("package.json", old, new)))


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = os.path.join(self.tmp, "tests", "test_order.py")
        os.makedirs(os.path.dirname(self.f))
        with open(self.f, "w") as fh:
            fh.write(PY_OLD)

    def test_edit_removing_assert_asks(self):
        inp = {"file_path": self.f, "old_string": '    assert order.currency == "EUR"\n', "new_string": ""}
        self.assertEqual(hook("Edit", inp, cwd=self.tmp), "ask")

    def test_block_mode_denies(self):
        inp = {"file_path": self.f, "old_string": '    assert order.currency == "EUR"\n', "new_string": ""}
        self.assertEqual(hook("Edit", inp, env={"TEST_GUARD_MODE": "block"}, cwd=self.tmp), "deny")

    def test_off(self):
        inp = {"file_path": self.f, "old_string": '    assert order.currency == "EUR"\n', "new_string": ""}
        self.assertEqual(hook("Edit", inp, env={"TEST_GUARD_MODE": "off"}, cwd=self.tmp), "allow")

    def test_write_adding_test_allows(self):
        self.assertEqual(hook("Write", {"file_path": self.f, "content": PY_OLD + "\ndef test_x():\n    assert f() == 1\n"}, cwd=self.tmp), "allow")

    def test_multiedit(self):
        inp = {"file_path": self.f, "edits": [{"old_string": "assert order.total == 42", "new_string": "assert True"}]}
        self.assertEqual(hook("MultiEdit", inp, cwd=self.tmp), "ask")

    def test_non_test_file_ignored(self):
        src = os.path.join(self.tmp, "order.py")
        self.assertEqual(hook("Write", {"file_path": src, "content": "assert True\n"}, cwd=self.tmp), "allow")

    def test_bash(self):
        self.assertEqual(hook("Bash", {"command": "pytest || true"}, cwd=self.tmp), "ask")
        self.assertEqual(hook("Bash", {"command": "pytest -q"}, cwd=self.tmp), "allow")

    def test_allowlist(self):
        with open(os.path.join(self.tmp, ".testguardignore"), "w") as fh:
            fh.write("tests/*\n")
        inp = {"file_path": self.f, "old_string": '    assert order.currency == "EUR"\n', "new_string": ""}
        self.assertEqual(hook("Edit", inp, cwd=self.tmp), "allow")

    def test_bad_input(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "guard.py")], input="not json", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class ScanTests(unittest.TestCase):
    def run_scan(self, repo, *args):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "scan.py"), repo, "--json", *args], capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout)

    def test_diff_and_snapshot(self):
        repo = tempfile.mkdtemp()
        g = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True)  # noqa: E731
        g("init", "-q")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        os.makedirs(os.path.join(repo, "tests"))
        for name, text in (("tests/test_order.py", PY_OLD), ("tests/test_old.py", PY_OLD), ("src.py", "x = 1\n")):
            with open(os.path.join(repo, name), "w") as fh:
                fh.write(text)
        g("add", "-A")
        g("commit", "-qm", "init")
        code, out = self.run_scan(repo)
        self.assertEqual((code, out["findings"]), (0, []))
        with open(os.path.join(repo, "tests/test_order.py"), "w") as fh:
            fh.write(PY_OLD.replace('    assert order.currency == "EUR"\n', "").replace("def test_total", "@pytest.mark.skip\ndef test_total"))
        os.remove(os.path.join(repo, "tests/test_old.py"))
        code, out = self.run_scan(repo)
        self.assertEqual(code, 1)
        self.assertEqual(sorted(f["kind"] for f in out["findings"]), ["assert_removed", "skip_added", "test_deleted"])
        code, out = self.run_scan(repo, "--snapshot")
        self.assertEqual([f["kind"] for f in out["findings"]], ["skips"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
