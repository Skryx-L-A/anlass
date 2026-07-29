"""Stage 10: read replies back in, classify them, schedule follow-up.

This is the reason the project exists in the first place: the previous tool had
everything except a return channel, so it optimised blind.
``imap.py`` fetches and correlates, ``classify.py`` decides what a reply means,
``followup.py`` sets a due date and never sends anything by itself.
"""

from __future__ import annotations

from ..errors import AnlassError

__all__ = ["TrackError"]


class TrackError(AnlassError):
    """A tracker could not fetch or correlate replies. Never carries a secret."""
