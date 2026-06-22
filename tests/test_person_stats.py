from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gchat.person_stats import compute_mtld, tokenize_content


class PersonStatsTests(unittest.TestCase):
    def test_tokenize_content_filters_stop_words(self) -> None:
        tokens = tokenize_content("The quick brown fox and the lazy dog")
        self.assertEqual(tokens, ["quick", "brown", "fox", "lazy", "dog"])

    def test_compute_mtld_empty(self) -> None:
        self.assertEqual(compute_mtld([]), 0.0)

    def test_compute_mtld_single_token(self) -> None:
        self.assertEqual(compute_mtld(["hello"]), 1.0)

    def test_compute_mtld_repeated_tokens_lower_than_varied(self) -> None:
        repeated = ["word"] * 20
        varied = [f"word{i}" for i in range(20)]
        self.assertLess(compute_mtld(repeated), compute_mtld(varied))

    def test_compute_mtld_is_symmetric(self) -> None:
        tokens = ["alpha", "beta", "gamma", "alpha", "delta", "beta"]
        forward = compute_mtld(tokens)
        backward = compute_mtld(list(reversed(tokens)))
        self.assertAlmostEqual(forward, backward)


if __name__ == "__main__":
    unittest.main()
