"""Waterfall providers for stage 3: page fetch, org-website guess, prose extraction."""

from __future__ import annotations

from .extract import ExtractionProvider
from .orgsite import OrgWebsiteProvider
from .page import PageTextProvider

__all__ = ["ExtractionProvider", "OrgWebsiteProvider", "PageTextProvider"]
