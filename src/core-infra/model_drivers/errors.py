"""Normalized executable DriverAdapter failures."""

from __future__ import annotations


class DriverError(Exception):
    """Provider-independent adapter failure safe to surface through a runner."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
