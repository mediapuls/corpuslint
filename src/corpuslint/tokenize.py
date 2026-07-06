from __future__ import annotations


def count_tokens(text: str) -> int:
    """MVP token approximation: whitespace-delimited word count."""
    return len(text.split())
