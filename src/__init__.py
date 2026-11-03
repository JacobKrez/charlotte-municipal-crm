"""Charlotte municipal CRM case study: small, testable, dependency-free.

The only executable artifact in this repository is the deflection arithmetic
in :mod:`src.roi`. It is deliberately pure: no I/O, no clock, no randomness, no
network. Every number it produces is a function of its arguments plus the
constants declared in that module, each of which carries a provenance note.

See ``README.md`` for the framing and ``QUALITY_BAR.md`` for the thresholds
that gate this repository.
"""

__all__ = ["roi"]
