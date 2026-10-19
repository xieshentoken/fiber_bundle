"""Bounded location-change errors, safe to expose without filesystem diagnostics."""

from ..config import InvalidInput


class RelocationError(InvalidInput):
    def __init__(self, code: str, status: int = 409):
        if not code.startswith("relocation_") or status not in {404, 409, 422}:
            raise ValueError("invalid relocation error")
        self.status = status
        super().__init__(code)
