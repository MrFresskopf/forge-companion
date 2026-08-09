"""Shared terminal-boundary helpers for CLI command modules."""

import sys


def is_interactive_terminal() -> bool:
    """Require a human-attended terminal for implicit selection or hardware confirmation."""
    return sys.stdin.isatty() and sys.stdout.isatty()
