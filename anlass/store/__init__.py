"""Persistence. SQLite is the default: no setup, one file, runs immediately."""

from __future__ import annotations

from .sqlite import SCHEMA_VERSION, SqliteStore

__all__ = ["SCHEMA_VERSION", "SqliteStore"]
