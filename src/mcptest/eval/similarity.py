"""Lightweight, zero-dependency text similarity metrics.

All functions return a float in [0.0, 1.0] where 1.0 means perfect similarity
and 0.0 means no overlap at all.  No external libraries are required — the
implementations use only the Python standard library so tests remain fast and
CI-friendly.

Functions
---------
levenshtein_similarity
    Character-level edit distance normalised to [0, 1].
jaccard_similarity
    Token-level Jaccard index (size of intersection / size of union).
cosine_similarity_tfidf
    Word-frequency cosine similarity — approximates TF-IDF without IDF weights.
keyword_coverage
    Fraction of a keyword list found in the target text.
best_similarity
    Maximum of levenshtein, jaccard, and cosine scores.
"""

from __future__ import annotations

import math
import re
from collections import Counter


def levenshtein_similarity(a: str, b: str) -> float:
    """Return 1 - (edit_distance(a, b) / max(len(a), len(b))).

    Empty strings are considered identical (score 1.0).
    """
    if a == b:
        return 1.0
    len_a, len_b = len(a), len(b)
    if len_a == 0 or len_b == 0:
        return 0.0

    # Standard dynamic-programming Levenshtein with row compression.
    prev = list(range(len_b + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len_b
        for j, cb in enumerate(b, 1):
            if ca == cb:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev = curr

    edit_dist = prev[len_b]
    return 1.0 - edit_dist / max(len_a, len_b)


def _tokenize(text: str) -> list[str]:
    """Split *text* into lower-cased word tokens."""
    return re.findall(r"\w+", text.lower())


def jaccard_similarity(a: str, b: str) -> float:
    """Return the token-level Jaccard index: |A ∩ B| / |A ∪ B|.

    Both strings are tokenised (lower-cased words).  Returns 1.0 when both
    strings are empty, 0.0 when one is empty and the other is not.
    """
    set_a = set(_tokenize(a))
    set_b = set(_tokenize(b))
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def cosine_similarity_tfidf(a: str, b: str) -> float:
    """Word-frequency cosine similarity (no IDF weighting).

    Treats each string as a bag-of-words count vector and returns the cosine
    of the angle between the two vectors.  Returns 1.0 when both strings are
    empty, 0.0 when one is empty and the other is not.
    """
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    freq_a = Counter(tokens_a)
    freq_b = Counter(tokens_b)

    vocab = set(freq_a) | set(freq_b)
    dot = sum(freq_a[w] * freq_b[w] for w in vocab)
    mag_a = math.sqrt(sum(v * v for v in freq_a.values()))
    mag_b = math.sqrt(sum(v * v for v in freq_b.values()))

    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def keyword_coverage(
    text: str,
    keywords: list[str],
    *,
    case_sensitive: bool = False,
) -> float:
    """Return the fraction of *keywords* found in *text*.

    Args:
        text: The text to search within.
        keywords: List of keyword strings to look for (sub-string match).
        case_sensitive: When ``False`` (default), comparison is case-insensitive.

    Returns:
        A float in [0.0, 1.0].  Returns 1.0 if *keywords* is empty.
    """
    if not keywords:
        return 1.0
    search = text if case_sensitive else text.lower()
    found = sum(
        1
        for kw in keywords
        if (kw if case_sensitive else kw.lower()) in search
    )
    return found / len(keywords)


def best_similarity(a: str, b: str) -> float:
    """Return the maximum of levenshtein, jaccard, and cosine scores.

    This gives a generous "similarity ceiling" — if any metric finds strong
    overlap the score is high, making it suitable for short reference answers
    where lexical variation should not penalise heavily.
    """
    return max(
        levenshtein_similarity(a, b),
        jaccard_similarity(a, b),
        cosine_similarity_tfidf(a, b),
    )
