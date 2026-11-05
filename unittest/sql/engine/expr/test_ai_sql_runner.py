"""Tests for explicit mysqltest selection; no database process is started here."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "seekdb_mysqltest_runner", ROOT / ".github/script/seekdb/mysqltest_for_seekdb.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class SQLCaseSelectionTest(unittest.TestCase):
    def setUp(self):
        self.cases = [RUNNER.MysqltestCase(name, Path(name + ".test"), Path(name + ".result"))
                      for name in ("ordinary", "ai_function.ai_function_enhancement", "ai_function.ai_model_ddl")]

    def test_default_keeps_existing_selection(self):
        self.assertEqual(RUNNER.select_cases(self.cases, []), self.cases)

    def test_exact_selection_preserves_requested_order(self):
        names = ["ai_function.ai_model_ddl", "ai_function.ai_function_enhancement"]
        self.assertEqual([case.name for case in RUNNER.select_cases(self.cases, names)], names)

    def test_unknown_names_cannot_turn_into_successful_zero_test_runs(self):
        with self.assertRaisesRegex(RUNNER.RunnerError, "unknown or excluded"):
            RUNNER.select_cases(self.cases, ["ai_function.typo"])

    def test_duplicate_and_empty_selections_fail(self):
        with self.assertRaisesRegex(RUNNER.RunnerError, "duplicate"):
            RUNNER.select_cases(self.cases, ["ordinary", "ordinary"])
        with self.assertRaisesRegex(RUNNER.RunnerError, "no mysqltest"):
            RUNNER.select_cases([], [])

    def test_actual_official_acceptance_case_is_available(self):
        selected = RUNNER.select_cases(RUNNER.discover_cases(ROOT), ["ai_function.ai_function_enhancement"])
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].test_file.is_file())
        self.assertTrue(selected[0].result_file.is_file())

    def test_merge_parser_accepts_the_same_selection(self):
        args = RUNNER.create_parser().parse_args([
            "merge", "--results-dir", "/tmp/unused", "--slice-count", "1", "--output", "/tmp/unused.json",
            "--case", "ai_function.ai_function_enhancement"])
        self.assertEqual(args.cases, ["ai_function.ai_function_enhancement"])


class FailureAnnotationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "ai_acceptance_diagnostics", ROOT / ".github/script/seekdb/ai_acceptance_diagnostics.py")
        cls.diagnostics = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.diagnostics)

    def test_log_text_cannot_inject_workflow_commands(self):
        self.assertEqual(self.diagnostics.escape_annotation("100%\r\n::warning::text"),
                         "100%25%0D%0A::warning::text")

    def test_missing_log_is_explicit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as path:
            self.assertIn("No build or test log", self.diagnostics.last_failure_annotation(Path(path)))

    def test_log_tail_is_bounded_and_symlinks_are_ignored(self):
        import tempfile
        with tempfile.TemporaryDirectory() as path:
            folder = Path(path)
            (folder / "build.log").write_text("x" * 20000 + "\nactual compile error\n")
            (folder / "sql-tests.log").symlink_to(folder / "build.log")
            result = self.diagnostics.last_failure_annotation(folder)
            self.assertTrue(result.startswith("build.log\n"))
            self.assertTrue(result.endswith("actual compile error"))
            self.assertLessEqual(len(result), 6010)

    def test_all_sql_case_failures_are_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as path:
            folder = Path(path)
            for name, message in (("first.case", "first error"), ("second.case", "second error")):
                failure = folder / "sql" / "failures" / name
                failure.mkdir(parents=True)
                (failure / "mysqltest.log").write_text(message)
            result = self.diagnostics.last_failure_annotation(folder)
            self.assertIn("first.case\nfirst error", result)
            self.assertIn("second.case\nsecond error", result)
            self.assertLessEqual(len(result), 6000)


if __name__ == "__main__":
    unittest.main()
