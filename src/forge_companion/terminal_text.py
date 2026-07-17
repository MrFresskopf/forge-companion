"""Shared sanitization for untrusted terminal text."""

import re
import unicodedata


def safe_terminal_text(value: str, *, limit: int = 160) -> str:
    """Remove terminal controls, normalize whitespace, and bound output length."""
    value = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", value)
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character for character in value
    )
    normalized = " ".join(without_controls.split()).replace("|", "\\|")
    if len(normalized) > limit:
        return normalized[: limit - 3] + "..."
    return normalized
