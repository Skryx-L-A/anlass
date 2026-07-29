"""Stage 9: the pluggable way out (file, SMTP, webhook), plus deliverability preflight.

``file.py`` is the default because it does nothing to the outside world. ``smtp.py``
and ``webhook.py`` actually leave the machine and are the only place in this package
that does. ``preflight.py`` checks before either of them sends; it never sends itself.
"""

from __future__ import annotations

from ..errors import AnlassError

__all__ = ["TransportError"]


class TransportError(AnlassError):
    """A transport could not deliver, or could not even try. Never carries a secret."""
