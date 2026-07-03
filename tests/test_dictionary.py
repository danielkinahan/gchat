from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gchat.dictionary import is_dictionary_word, partition_dictionary_words


class DictionaryTests(unittest.TestCase):
    def test_common_words_are_dictionary_words(self) -> None:
        self.assertTrue(is_dictionary_word("hello"))
        self.assertTrue(is_dictionary_word("world"))

    def test_gibberish_is_not_dictionary_word(self) -> None:
        self.assertFalse(is_dictionary_word("xyzzyplugh"))
        self.assertFalse(is_dictionary_word("asdfghjkl"))

    def test_partition_dictionary_words(self) -> None:
        dictionary_words, other_words = partition_dictionary_words(
            ["hello", "xyzzyplugh", "world", "zztopfake"]
        )
        self.assertEqual(dictionary_words, ["hello", "world"])
        self.assertEqual(other_words, ["xyzzyplugh", "zztopfake"])


if __name__ == "__main__":
    unittest.main()
