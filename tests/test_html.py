"""Tests for the shareable artifacts: the HTML report and the SVG badge."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from baggage import analyze_baggage  # noqa: E402
from baggage.cost import Analysis, Hotspot  # noqa: E402
from baggage.html import render_badge_svg, render_html  # noqa: E402

from test_baggage import RepoBuilder  # noqa: E402


class HtmlReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        repo = RepoBuilder(cls.root)
        for i in range(6):
            repo.commit(
                {
                    "src/core.py": "\n".join(f"v{j} = {i}" for j in range(20)) + "\n",
                    "src/api/routes.py": f"r = {i}\n",
                    "tests/test_core.py": f"def test_{i}(): pass\n",
                }
            )
        cls.analysis = analyze_baggage(cls.root, top=50)
        cls.html = render_html(cls.analysis, generated_at="2026-01-01 00:00 UTC")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_is_a_complete_document(self):
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"))
        self.assertIn("<html", self.html)
        self.assertTrue(self.html.rstrip().endswith("</html>"))

    def test_is_self_contained(self):
        """A report you cannot open offline is a report nobody opens."""
        self.assertNotRegex(self.html, r"https?://")
        self.assertNotIn("<script src=", self.html)
        self.assertNotIn("<link ", self.html)

    def test_shows_score_and_grade(self):
        self.assertIn(f"{self.analysis.score:.1f}", self.html)
        self.assertIn(f">{self.analysis.grade}<", self.html)

    def test_lists_hotspots(self):
        self.assertIn("src/core.py", self.html)
        self.assertIn("Top hotspots", self.html)

    def test_excluded_categories_are_reported(self):
        self.assertIn("test", self.html)
        self.assertIn("skipped", self.html.lower() + "skipped")

    def test_escapes_untrusted_paths(self):
        """Repository paths end up in HTML, so they must be escaped.

        Built as a data object rather than on disk: Windows forbids ``<`` in
        filenames, which would make this test untestable on half the platforms
        the tool ships to.
        """
        nasty = "<script>alert(1)</script>.py"
        spot = Hotspot(
            path=nasty,
            score=61.0,
            commits=3,
            recent_commits=3,
            churn=120,
            loc=40,
            coupling=1,
            authors=2,
            is_tested=False,
            is_test=False,
            reasons=["<img src=x onerror=alert(1)>"],
        )
        analysis = Analysis(
            root=Path("/tmp/demo"),
            score=61.0,
            grade="D",
            hotspots=[spot],
            file_count=1,
            commit_count=5,
            span_days=400,
            total_churn=120,
        )
        out = render_html(analysis)
        self.assertNotIn(nasty, out)
        self.assertNotIn("<img src=x", out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", out)

    def test_sortable_headers_carry_numeric_values(self):
        self.assertIn('data-sort="score"', self.html)
        self.assertIn("data-v=", self.html)

    def test_handles_empty_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = RepoBuilder(root)
            repo.commit({"README.md": "# hi\n"})  # docs only, nothing scored
            out = render_html(analyze_baggage(root))
            self.assertIn("</html>", out)
            self.assertIn("No hotspots", out)


class BadgeTests(unittest.TestCase):
    def test_badge_contains_score_and_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = RepoBuilder(root)
            for i in range(4):
                repo.commit({"src/core.py": f"x = {i}\n"})
            analysis = analyze_baggage(root)
            svg = render_badge_svg(analysis)
            self.assertTrue(svg.startswith("<svg"))
            self.assertIn(f"{analysis.score:.1f} {analysis.grade}", svg)
            self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)

    def test_badge_width_matches_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = RepoBuilder(root)
            repo.commit({"src/core.py": "x = 1\n"})
            svg = render_badge_svg(analyze_baggage(root), label="custom-label")
            width = int(re.search(r'width="(\d+)"', svg).group(1))
            self.assertGreater(width, len("custom-label") * 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
