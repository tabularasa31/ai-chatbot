"""Writing-system detection derived from Unicode character data.

The bucket for a text is the dominant Unicode script among its letters, read
from each character's Unicode name rather than from a hand-listed alphabet.
Every writing system therefore gets its own bucket without the code naming a
single language, and new ones need no change here.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from functools import lru_cache

# Text with no letters at all (digits, punctuation, symbols, empty input).
NO_SCRIPT_BUCKET = "other"

# Scanning every character of a long document adds nothing: the dominant
# script of a prefix this size is the dominant script of the whole text.
_SCRIPT_SAMPLE_CHARS = 4096


@lru_cache(maxsize=4096)
def _character_script(char: str) -> str | None:
    """Return the writing system of a single letter, or None for non-letters.

    Taken from the leading token of the character's Unicode name. The token is
    also split on "-" so a character shared between two writing systems (whose
    name leads with both, joined) lands in one of them rather than in a bucket
    of its own that no corpus can ever match.
    """
    if not char.isalpha():
        return None
    try:
        name = unicodedata.name(char)
    except ValueError:
        return None
    return name.split(" ", 1)[0].split("-", 1)[0].casefold()


def detect_script_bucket(text: str | None) -> str:
    """Return the dominant writing system of ``text``.

    Ties resolve to the highest-sorting script name, so the result is stable
    for a given input.
    """
    if not text:
        return NO_SCRIPT_BUCKET
    # Compatibility normalization folds presentation forms (fullwidth,
    # halfwidth, mathematical, superscript) onto the letters they stand for,
    # so they bucket by writing system rather than by presentation.
    sample = unicodedata.normalize("NFKC", text[:_SCRIPT_SAMPLE_CHARS])
    counts: Counter[str] = Counter()
    for char in sample:
        script = _character_script(char)
        if script is not None:
            counts[script] += 1
    if not counts:
        return NO_SCRIPT_BUCKET
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]
