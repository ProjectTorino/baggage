"""Regression test for the thing that actually matters: ranking quality.

The benchmark plants two known money pits, one decoy that is touched constantly
but only ever changes two lines, and a pile of cold filler. If a change to the
cost model reorders these, this test fails loudly.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import build_benchmark  # noqa: E402
from baggage import analyze_baggage  # noqa: E402


class RankingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = build_benchmark.build(Path(cls._tmp.name) / "repo")
        cls.analysis = analyze_baggage(cls.repo, top=100)
        cls.order = [h.path for h in cls.analysis.hotspots]
        cls.score = {h.path: h.score for h in cls.analysis.hotspots}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_planted_money_pit_ranks_first(self):
        self.assertEqual(self.order[0], "src/core/engine.py")

    def test_large_legacy_file_is_in_the_top_three(self):
        self.assertIn("src/legacy/monolith.py", self.order[:3])

    def test_frequently_touched_two_line_file_is_not_a_hotspot(self):
        decoy = self.score["src/utils/format.py"]
        pit = self.score["src/core/engine.py"]
        self.assertGreater(pit, decoy * 2, f"decoy scored {decoy} vs pit {pit}")

    def test_decoy_ranks_below_the_tested_module(self):
        self.assertGreater(
            self.order.index("src/utils/format.py"),
            self.order.index("src/api/routes.py"),
        )

    def test_cold_filler_sinks_to_the_bottom(self):
        tail = set(self.order[-5:])
        self.assertTrue(
            any(path.startswith(("docs/", "scripts/", "web/")) for path in tail),
            f"expected filler at the bottom, got {tail}",
        )

    def test_scores_stay_in_range(self):
        for spot in self.analysis.hotspots:
            self.assertGreaterEqual(spot.score, 0.0)
            self.assertLessEqual(spot.score, 100.0)

    def test_engine_reports_why_it_hurts(self):
        top = self.analysis.hotspots[0]
        self.assertTrue(top.reasons)
        self.assertTrue(any("churn" in r or "ripple" in r for r in top.reasons))


if __name__ == "__main__":
    unittest.main(verbosity=2)
