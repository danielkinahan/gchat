"""English dictionary lookup for solo-word classification."""

from __future__ import annotations

import importlib.resources

_ENGLISH_WORDS: frozenset[str] | None = None


def _words_from_text(text: str) -> set[str]:
    return {
        word
        for line in text.splitlines()
        for word in [line.strip().lower()]
        if word.isalpha() and len(word) >= 3
    }


def load_english_words() -> frozenset[str]:
    global _ENGLISH_WORDS
    if _ENGLISH_WORDS is not None:
        return _ENGLISH_WORDS

    text = (
        importlib.resources.files("gchat")
        .joinpath("data/english_words.txt")
        .read_text(encoding="utf-8")
    )
    _ENGLISH_WORDS = frozenset(_words_from_text(text))
    return _ENGLISH_WORDS


def is_dictionary_word(word: str) -> bool:
    return word.lower() in load_english_words()


def partition_dictionary_words(words: list[str]) -> tuple[list[str], list[str]]:
    """Return (dictionary words, non-dictionary words), preserving sort order."""
    dictionary_words: list[str] = []
    other_words: list[str] = []
    for word in words:
        if is_dictionary_word(word):
            dictionary_words.append(word)
        else:
            other_words.append(word)
    return dictionary_words, other_words
