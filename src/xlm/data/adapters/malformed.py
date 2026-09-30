"""Deterministic per-pass malformed row allowance (not selector tuning)."""

from dataclasses import dataclass


class MalformedLimitError(ValueError):
    """Malformed input exceeds the production allowance; stop the pass."""


@dataclass
class MalformedCounter:
    """At 100 rows enforce <=1%; before then allow at most two bad rows."""

    rows: int = 0
    malformed: int = 0

    def observe(self, malformed: bool) -> None:
        self.rows += 1
        self.malformed += int(malformed)
        if (self.rows < 100 and self.malformed > 2) or (
            self.rows >= 100 and self.malformed * 100 > self.rows
        ):
            raise MalformedLimitError("essential_web_malformed_fraction_exceeded")
