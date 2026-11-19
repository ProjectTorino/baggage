import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from baggage import analyze_baggage
from baggage.cost import classify, grade_for, is_distant, is_test_file, module_of
from baggage.gitlog import GitError, read_history, resolve_rename
from baggage.report import render_json, render_text


class RepoBuilder:
    """Create a throwaway git repo with deterministic commits."""

    def __init__(self, root: Path, step_days: int = 7):
        self.root = root
        self.step_days = step_days
        self._day = 0
        self.git("init", "-q", "-b", "main")

    def _stamp(self) -> str:
        moment = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(days=self._day)
        return moment.strftime("%Y-%m-%dT%H:%M:%S +0000")

    def _run(self, args: list[str], author: str) -> None:
        env = os.environ.copy()
        stamp = self._stamp()
        env.update(
            {
                "GIT_AUTHOR_NAME": author,
                "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
                "GIT_COMMITTER_NAME": author,
                "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com",
                "GIT_AUTHOR_DATE": stamp,
                "GIT_COMMITTER_DATE": stamp,
            }
        )
        subprocess.run(
            ["git", "-C", str(self.root), *args], env=env, check=True, capture_output=True
        )

    def git(self, *args: str) -> None:
        self._run(list(args), "Alice")

    def commit(self, files: dict[str, str], author: str = "Alice") -> None:
        self._day += self.step_days
        for rel, content in files.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self._run(["add", "-A"], author)
        self._run(["commit", "-q", "-m", f"change {self._day}"], author)


class RenameTests(unittest.TestCase):
    def test_plain_path(self):
        self.assertEqual(resolve_rename("src/a.py"), "src/a.py")

    def test_simple_rename(self):
        self.assertEqual(resolve_rename("old.py => new.py"), "new.py")

    def test_braced_rename(self):
        self.assertEqual(resolve_rename("src/{old => new}/engine.py"), "src/new/engine.py")

    def test_braced_move_into_subdir(self):
        self.assertEqual(resolve_rename("src/{ => nested}/engine.py"), "src/nested/engine.py")

    def test_braced_move_up(self):
        self.assertEqual(resolve_rename("src/{deep/ => }engine.py"), "src/engine.py")


class PathTests(unittest.TestCase):
    def test_test_detection(self):
        self.assertTrue(is_test_file("tests/test_a.py"))
        self.assertTrue(is_test_file("src/test_engine.py"))
        self.assertTrue(is_test_file("src/engine_test.go"))
        self.assertTrue(is_test_file("web/app.test.ts"))
        self.assertTrue(is_test_file("pkg/conftest.py"))
        self.assertFalse(is_test_file("src/engine.py"))
        self.assertFalse(is_test_file("src/testing_utils.py"))

    def test_module_of(self):
        self.assertEqual(module_of("src/a.py"), "src")
        self.assertEqual(module_of("a.py"), "")
        self.assertEqual(module_of("src/core/a.py"), "src/core")

    def test_distant_means_ripples_outside_the_folder(self):
        self.assertTrue(is_distant("src/core/a.py", "src/api/b.py"))
        self.assertTrue(is_distant("src/a.py", "src/core/b.py"))
        self.assertTrue(is_distant("a.py", "src/b.py"))
        self.assertFalse(is_distant("src/core/a.py", "src/core/b.py"))
        self.assertFalse(is_distant("a.py", "b.py"))


class GradeTests(unittest.TestCase):
    def test_bands_are_ordered(self):
        self.assertEqual(grade_for(0), "A")
        self.assertEqual(grade_for(25), "B")
        self.assertEqual(grade_for(40), "C")
        self.assertEqual(grade_for(60), "D")
        self.assertEqual(grade_for(90), "F")


class ClassifyTests(unittest.TestCase):
    """Mechanical churn must not outrank code. These cases come from real repos."""

    def test_source_files_are_scored(self):
        self.assertEqual(classify("src/flask/app.py"), "source")
        self.assertEqual(classify("web/static/app.js"), "source")
        self.assertEqual(classify("scripts/deploy.sh"), "source")
        self.assertEqual(classify("Makefile.am"), "packaging")

    def test_lockfiles(self):
        self.assertEqual(classify("uv.lock"), "lockfile")
        self.assertEqual(classify("package-lock.json"), "lockfile")
        self.assertEqual(classify("go.sum"), "lockfile")
        self.assertEqual(classify("requirements.lock"), "lockfile")

    def test_ci_config(self):
        self.assertEqual(classify(".github/workflows/tests.yaml"), "ci")
        self.assertEqual(classify(".gitlab-ci.yml"), "ci")
        self.assertEqual(classify("Jenkinsfile"), "ci")

    def test_docs_by_extension_and_directory(self):
        self.assertEqual(classify("HISTORY.md"), "docs")
        self.assertEqual(classify("docs/_static/custom.css"), "docs")
        self.assertEqual(classify("website/index.html"), "docs")

    def test_samples(self):
        self.assertEqual(classify("examples/celery/src/app.py"), "sample")

    def test_packaging(self):
        self.assertEqual(classify("setup.py"), "packaging")
        self.assertEqual(classify("Dockerfile"), "packaging")
        self.assertEqual(classify("tox.ini"), "packaging")

    def test_vendored_and_generated(self):
        self.assertEqual(classify("node_modules/x/index.js"), "vendored")
        self.assertEqual(classify("vendor/lib.go"), "vendored")
        self.assertEqual(classify("static/app.min.js"), "generated")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = RepoBuilder(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_not_a_repository(self):
        with tempfile.TemporaryDirectory() as plain:
            with self.assertRaises(GitError):
                read_history(plain)

    def test_counts_commits_and_churn(self):
        self.repo.commit({"a.py": "x = 1\n"})
        self.repo.commit({"a.py": "x = 2\ny = 3\n"})
        history = read_history(self.root)
        self.assertEqual(history.commit_count, 2)
        self.assertEqual(history.files["a.py"].commits, 2)
        self.assertGreater(history.files["a.py"].churn, 0)
        self.assertEqual(history.files["a.py"].author_count, 1)

    def test_authors_are_tracked(self):
        self.repo.commit({"a.py": "x\n"}, author="Alice")
        self.repo.commit({"a.py": "y\n"}, author="Bob")
        history = read_history(self.root)
        self.assertEqual(history.files["a.py"].author_count, 2)

    def test_recent_window_counts_only_new_tail(self):
        self.repo.commit({"a.py": "x\n"})
        history = read_history(self.root, recent_days=365)
        self.assertEqual(history.files["a.py"].recent_commits, 1)
        # A zero-day window must count nothing.
        none_recent = read_history(self.root, recent_days=0)
        self.assertEqual(none_recent.files["a.py"].recent_commits, 0)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = RepoBuilder(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_hot_file_outranks_cold_file(self):
        self.repo.commit({"src/cold.py": "x = 1\n"})
        for i in range(6):
            self.repo.commit(
                {
                    "src/core.py": "\n".join(f"v{j} = {i}" for j in range(10 + i)) + "\n",
                    "src/other.py": f"y = {i}\n",
                }
            )
        analysis = analyze_baggage(self.root, top=5)
        self.assertTrue(analysis.hotspots)
        self.assertEqual(analysis.hotspots[0].path, "src/core.py")

    def test_scores_are_bounded(self):
        for i in range(4):
            self.repo.commit({"src/core.py": f"x = {i}\n"})
        analysis = analyze_baggage(self.root)
        for spot in analysis.hotspots:
            self.assertGreaterEqual(spot.score, 0.0)
            self.assertLessEqual(spot.score, 100.0)
        self.assertGreaterEqual(analysis.score, 0.0)
        self.assertLessEqual(analysis.score, 100.0)

    def test_test_files_excluded_by_default(self):
        for i in range(5):
            self.repo.commit(
                {
                    "src/core.py": f"x = {i}\n",
                    "tests/test_core.py": f"def test_{i}(): pass\n",
                }
            )
        default = analyze_baggage(self.root)
        self.assertNotIn("tests/test_core.py", [h.path for h in default.hotspots])
        self.assertEqual(default.excluded.get("test"), 1)

        included = analyze_baggage(self.root, include_tests=True)
        self.assertIn("tests/test_core.py", [h.path for h in included.hotspots])

    def test_tested_flag_is_set_when_tests_move_with_code(self):
        for i in range(4):
            self.repo.commit(
                {
                    "src/core.py": f"x = {i}\n",
                    "tests/test_core.py": f"def test_{i}(): pass\n",
                }
            )
        analysis = analyze_baggage(self.root)
        core = next(h for h in analysis.hotspots if h.path == "src/core.py")
        self.assertTrue(core.is_tested)

    def test_coupling_counts_distant_partners(self):
        for i in range(3):
            self.repo.commit(
                {"src/core.py": f"x = {i}\n", "src/api/routes.py": f"r = {i}\n"}
            )
        analysis = analyze_baggage(self.root)
        core = next(h for h in analysis.hotspots if h.path == "src/core.py")
        self.assertGreaterEqual(core.coupling, 1)

    def test_tests_are_not_coupling_partners(self):
        """Moving with your own test is health, not a hidden dependency."""
        for i in range(4):
            self.repo.commit(
                {
                    "src/core.py": f"x = {i}\n",
                    "tests/test_core.py": f"def test_{i}(): pass\n",
                }
            )
        analysis = analyze_baggage(self.root)
        core = next(h for h in analysis.hotspots if h.path == "src/core.py")
        self.assertEqual(core.coupling, 0)
        self.assertTrue(core.is_tested)

    def test_deleted_files_are_not_hotspots(self):
        self.repo.commit({"src/gone.py": "x = 1\n"})
        self.repo.git("rm", "-q", "src/gone.py")
        self.repo.commit({"src/keep.py": "y = 1\n"})
        analysis = analyze_baggage(self.root)
        self.assertNotIn("src/gone.py", [h.path for h in analysis.hotspots])

    def test_reasons_are_produced(self):
        for i in range(5):
            self.repo.commit({"src/core.py": "\n".join(f"v{j}={i}" for j in range(30)) + "\n"})
        analysis = analyze_baggage(self.root)
        core = analysis.hotspots[0]
        self.assertTrue(core.reasons)
        self.assertLessEqual(len(core.reasons), 3)


class WarningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = RepoBuilder(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_short_history_is_flagged_unreliable(self):
        self.repo.commit({"a.py": "x = 1\n"})
        analysis = analyze_baggage(self.root)
        self.assertFalse(analysis.is_reliable)
        self.assertTrue(any("of history" in w for w in analysis.warnings))

    def test_long_history_is_trusted(self):
        for i in range(25):
            self.repo.commit({"a.py": f"x = {i}\n"})
        analysis = analyze_baggage(self.root)
        self.assertTrue(analysis.is_reliable, analysis.warnings)

    def test_warnings_reach_json(self):
        self.repo.commit({"a.py": "x = 1\n"})
        payload = json.loads(render_json(analyze_baggage(self.root)))
        self.assertFalse(payload["reliable"])
        self.assertTrue(payload["warnings"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = RepoBuilder(self.root)
        for i in range(3):
            self.repo.commit({"src/core.py": f"x = {i}\n", "pyproject.toml": "[a]\n"})

    def tearDown(self):
        self._tmp.cleanup()

    def test_text_report_has_header_and_table(self):
        text = render_text(analyze_baggage(self.root), use_color=False)
        self.assertIn("baggage", text)
        self.assertIn("/ 100", text)
        self.assertIn("Top hotspots", text)
        self.assertIn("src/core.py", text)

    def test_no_color_emits_no_escapes(self):
        text = render_text(analyze_baggage(self.root), use_color=False)
        self.assertNotIn("\x1b[", text)

    def test_json_is_valid_and_complete(self):
        payload = json.loads(render_json(analyze_baggage(self.root)))
        self.assertIn("score", payload)
        self.assertIn("grade", payload)
        self.assertTrue(payload["hotspots"])
        first = payload["hotspots"][0]
        for key in ("path", "score", "commits", "churn", "loc", "reasons"):
            self.assertIn(key, first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
