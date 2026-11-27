"""Shared checks for test-integrity-guard.

Compares a test file before and after a change and reports what got weaker:
  - assertions removed (fewer assert / expect / Assert.* calls than before)
  - strict checks swapped for loose ones (toEqual -> toBeTruthy, assertEqual -> assertIsNotNone)
  - tests switched off (skip, xit, @Ignore, [Fact(Skip=...)], t.Skip, #[ignore])
  - focus markers that silently stop the other tests (it.only, fdescribe)
  - assertions that can never fail (assert True, expect(true).toBe(true))
  - failures swallowed inside a test (except: pass, catch {})
Also checks shell commands and CI files for ways to make a red test run look green.

Pure python3 standard library. No network, no files written.
"""
import os
import re

TEST_FILE_RES = [
    r"(^|/)test_[^/]+\.py$", r"(^|/)[^/]+_test\.py$", r"(^|/)conftest\.py$",
    r"\.(test|spec)\.[cm]?[jt]sx?$", r"(^|/)__tests__/[^/]+\.[cm]?[jt]sx?$",
    r"(^|/)[^/]+_test\.go$",
    r"(^|/)[^/]*Tests?\.cs$", r"(^|/)[^/]*\.Tests?/.+\.cs$",
    r"(^|/)[^/]*(Test|Tests|IT)\.(java|kt)$", r"(^|/)src/test/.+\.(java|kt)$",
    r"(^|/)[^/]+_spec\.rb$", r"(^|/)spec/.+_spec\.rb$",
    r"(^|/)[^/]+Tests?\.swift$",
    r"(^|/)tests/.+\.rs$",
    r"(^|/)tests?/.+\.(py|[cm]?[jt]sx?|php)$", r"(^|/)[^/]+Test\.php$",
]

# A call that checks something. Counted per occurrence.
ASSERT_RE = re.compile(
    r"(?<![\w.])assert\b(?!\s*\()"              # python: assert x == y
    r"|\bassert\w*\s*\("                        # assert(...), assertEqual(...), assert_eq!(...) (via below)
    r"|\bself\.assert\w*\s*\("
    r"|\bassert\.\w+\s*\("                      # node:assert / chai assert.equal
    r"|\bexpect\s*\("                           # jest / vitest / chai / rspec
    r"|\.should(\.|\(|\s)"                      # chai should / FluentAssertions-like
    r"|\.Should\(\)"                            # FluentAssertions
    r"|\bAssert\.\w+\s*\("                      # xUnit / NUnit / MSTest / JUnit
    r"|\bAssertions\.\w+\s*\("
    r"|\bassert(?:_eq|_ne)?!\s*\("              # rust
    r"|\b(?:require|assert)\.\w+\s*\(\s*t\b"    # go testify
    r"|\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\("  # go testing
    r"|\bXCTAssert\w*\s*\("
    r"|\$this->assert\w+\s*\("
)

# Strict matchers vs loose ones. Swapping the first for the second hides wrong values.
STRICT_RE = re.compile(
    r"\.(toBe|toEqual|toStrictEqual|toMatchObject|toHaveBeenCalledWith|toHaveLength|toContain|toThrow\w*)\s*\("
    r"|\b(assertEqual|assertEquals|assertDictEqual|assertListEqual|assertRaises\w*|assert_equal|assert_eq!)\s*\("
    r"|\bAssert\.(Equal|AreEqual|Same|Throws\w*|StrictEqual|Single|Collection)\s*\("
    r"|\.(eq|eql|equal|deep\.equal)\s*\("
    r"|\bassert\s+[^\n#]*(==|!=|\bis\b|\bin\b)"
)
LOOSE_RE = re.compile(
    r"\.(toBeTruthy|toBeDefined|toBeFalsy|toBeUndefined)\s*\(\s*\)"
    r"|\.not\.toBeNull\s*\(\s*\)"
    r"|\.(toBeGreaterThan|toBeGreaterThanOrEqual)\s*\(\s*0\s*\)"
    r"|\b(assertIsNotNone|assertTrue|assertFalse|assertIsInstance|assert_not_nil)\s*\("
    r"|\bAssert\.(NotNull|IsNotNull|True|IsTrue|NotEmpty)\s*\("
    r"|\.(ok|exist)\b"
    r"|\bexpect\.any\s*\(\s*\w+\s*\)"
    r"|\bexpect\.anything\s*\(\s*\)"
)

SKIP_RE = re.compile(
    r"\b(?:it|test|describe|context|suite|specify)\.(?:skip|todo|failing)\b"
    r"|(?<![\w.])x(?:it|describe|test|context|specify)\s*\("
    r"|@pytest\.mark\.(?:skip|skipif|xfail)\b"
    r"|@unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b"
    r"|\bpytest\.(?:skip|xfail)\s*\("
    r"|\bself\.skipTest\s*\("
    r"|\[\s*(?:Fact|Theory)\s*\([^)\]]*\bSkip\s*="
    r"|\[\s*Ignore\b"
    r"|@(?:Disabled|Ignore)\b"
    r"|\bt\.Skip(?:f|Now)?\s*\("
    r"|#\[\s*ignore\b"
    r"|\bpending\b\s*(?:\(|['\"])"
    r"|\bmarkTestSkipped\s*\("
)
ONLY_RE = re.compile(
    r"\b(?:it|test|describe|context|suite)\.only\s*\("
    r"|(?<![\w.])f(?:it|describe|context)\s*\("
)
TRIVIAL_RE = re.compile(
    r"(?<![\w.])assert\s+(?:True|1|not\s+False|not\s+None)\s*(?:$|#|,)"
    r"|\bassert(?:True|_)?\s*\(\s*(?:True|true|1)\s*[,)]"
    r"|\bself\.assertTrue\s*\(\s*True\s*[,)]"
    r"|\bexpect\s*\(\s*(true|1|false)\s*\)\s*\.\s*(?:toBe|toEqual)\s*\(\s*\1\s*\)"
    r"|\bAssert\.(?:True|IsTrue)\s*\(\s*true\s*[,)]"
    r"|\bassert!\s*\(\s*true\s*\)"
    r"|\bself\.assertEqual\s*\(\s*(\w+)\s*,\s*\2\s*\)",
    re.M,
)
SWALLOW_RE = re.compile(
    r"except(?:\s+[\w.]+(?:\s+as\s+\w+)?)?\s*:\s*(?:\n\s*)?pass\b"
    r"|catch\s*(?:\([^)]*\))?\s*\{\s*\}"
)

LINE_COMMENT = re.compile(r"(?m)(^|\s)(#(?!\[)|//).*$")


def is_test_file(path):
    p = path.replace("\\", "/")
    return any(re.search(r, p) for r in TEST_FILE_RES)


def strip_comments(text):
    """Drop line comments so commented-out assertions don't count as assertions."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return LINE_COMMENT.sub(r"\1", text)


def metrics(text):
    code = strip_comments(text or "")
    return {
        "asserts": len(ASSERT_RE.findall(code)),
        "strict": len(STRICT_RE.findall(code)),
        "loose": len(LOOSE_RE.findall(code)),
        "skips": len(SKIP_RE.findall(code)),
        "only": len(ONLY_RE.findall(code)),
        "trivial": len(TRIVIAL_RE.findall(code)),
        "swallow": len(SWALLOW_RE.findall(code)),
    }


def _line_of(text, pattern):
    for i, ln in enumerate((text or "").splitlines(), 1):
        if pattern.search(strip_comments(ln)):
            return i
    return None


def compare(old, new):
    """Findings for a test file going from `old` to `new` text. Each: (kind, message)."""
    a, b = metrics(old), metrics(new)
    out = []
    if b["asserts"] < a["asserts"]:
        n = a["asserts"] - b["asserts"]
        out.append(("assert_removed", f"{n} assertion{'s' if n > 1 else ''} removed ({a['asserts']} -> {b['asserts']})"))
    if b["strict"] < a["strict"] and b["loose"] > a["loose"]:
        out.append(("assert_loosened", f"strict checks replaced by loose ones (strict {a['strict']} -> {b['strict']}, "
                                       f"loose {a['loose']} -> {b['loose']}), e.g. toEqual -> toBeTruthy"))
    if b["skips"] > a["skips"]:
        out.append(("skip_added", f"{b['skips'] - a['skips']} test(s) switched off (skip / xit / Ignore), line {_line_of(new, SKIP_RE)}"))
    if b["only"] > a["only"]:
        out.append(("only_added", f"a focus marker (.only / fit) was added at line {_line_of(new, ONLY_RE)}: every other test stops running"))
    if b["trivial"] > a["trivial"]:
        out.append(("trivial_assert", f"an assertion that can never fail was added at line {_line_of(new, TRIVIAL_RE)} (e.g. assert True)"))
    if b["swallow"] > a["swallow"]:
        out.append(("swallowed_error", f"an error is swallowed inside a test at line {_line_of(new, SWALLOW_RE)} (except: pass / catch {{}})"))
    return out


# ---- shell commands and CI files ------------------------------------------------------------------

TEST_CMD = r"(pytest|py\.test|jest|vitest|mocha|dotnet\s+test|go\s+test|cargo\s+test|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|yarn\s+(run\s+)?test|bun\s+test|rspec|phpunit|mvn\s+\S*test|gradle\w*\s+test|unittest)"
CMD_RULES = [
    ("test_cmd_or_true", re.compile(TEST_CMD + r"[^\n;&|]*(\|\|\s*(true|:|exit\s+0)\b|;\s*exit\s+0\b)"),
     "the test command is followed by `|| true` / `exit 0`, so a failing run reports success"),
    ("pass_with_no_tests", re.compile(r"--passWithNoTests\b"),
     "`--passWithNoTests` lets an empty or broken test selection pass"),
    ("deselect", re.compile(TEST_CMD + r"[^\n]*(\s-k\s+['\"]?not\s|--deselect\b|--ignore(=|\s)\S*test)"),
     "the run excludes tests (-k 'not ...' / --deselect / --ignore), so they can no longer fail"),
    ("update_snapshots", re.compile(TEST_CMD + r"[^\n]*(\s-u\b|--updateSnapshot\b|--update-snapshots\b|--snapshot-update\b)"),
     "snapshots are being overwritten with the current output: any wrong output becomes the new 'expected'"),
]
RM_TEST_RE = re.compile(r"(?:^|[;&|]\s*|\s)(?:git\s+rm|rm|unlink|del)\s+(?:-\S+\s+)*([^\n;&|]+)")
CI_RULES = [
    ("ci_continue_on_error", re.compile(r"continue-on-error:\s*true"),
     "`continue-on-error: true` in CI lets a red test step pass"),
    ("ci_allow_failure", re.compile(r"allow_failure:\s*true"),
     "`allow_failure: true` in CI lets a red test job pass"),
] + [(k, re.compile(r.pattern), m) for k, r, m in CMD_RULES]


def command_findings(cmd):
    out = [(k, m) for k, r, m in CMD_RULES if r.search(cmd or "")]
    for m in RM_TEST_RE.finditer(cmd or ""):
        targets = [t for t in m.group(1).split() if not t.startswith("-")]
        hits = [t for t in targets if is_test_file(t) or re.search(r"(^|/)(tests?|__tests__|spec)/?$", t)]
        if hits:
            out.append(("test_deleted", f"the command deletes test files ({', '.join(hits[:3])})"))
    return out


def is_ci_file(path):
    p = path.replace("\\", "/")
    return bool(re.search(r"(^|/)\.github/workflows/[^/]+\.ya?ml$|(^|/)\.gitlab-ci\.ya?ml$|(^|/)azure-pipelines\.ya?ml$"
                          r"|(^|/)\.circleci/config\.ya?ml$|(^|/)bitbucket-pipelines\.ya?ml$|(^|/)Jenkinsfile$", p))


def ci_findings(old, new):
    out = []
    for k, r, m in CI_RULES:
        if len(r.findall(new or "")) > len(r.findall(old or "")):
            out.append((k, m))
    return out


def file_findings(path, old, new):
    if is_test_file(path):
        return compare(old, new)
    if is_ci_file(path):
        return ci_findings(old, new)
    if os.path.basename(path) == "package.json":
        return [(k, m) for k, r, m in CMD_RULES if len(r.findall(new or "")) > len(r.findall(old or ""))]
    return []
