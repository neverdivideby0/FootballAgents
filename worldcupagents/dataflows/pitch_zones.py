"""Pitch zones — spatial coordinates → pundit language (roadmap B2).

Event providers give raw X,Y; LLMs reason better in football words. This module
translates StatsBomb's 120×80 frame (x: 0 at own goal line → 120 at the
opponent's; y: 0 at the LEFT touchline of the attacking direction → 80 at the
right) into the standard coaching grid:

    thirds (x):  defensive third | middle third | final third
    lanes  (y):  left wing | left half-space | central | right half-space | right wing

e.g. (105, 30) → "final third, left half-space";  (15, 78) → "defensive third,
right wing". No LLM ever sees raw coordinates — only these labels.
"""

from __future__ import annotations

_LENGTH, _WIDTH = 120.0, 80.0

_THIRDS = ("defensive third", "middle third", "final third")
_LANES = ("left wing", "left half-space", "central", "right half-space", "right wing")


def third(x: float) -> str:
    """Which third of the pitch an x-coordinate falls in (clamped)."""
    x = min(max(float(x), 0.0), _LENGTH)
    idx = min(int(x / (_LENGTH / 3)), 2)
    return _THIRDS[idx]


def lane(y: float) -> str:
    """Which vertical lane a y-coordinate falls in (clamped)."""
    y = min(max(float(y), 0.0), _WIDTH)
    idx = min(int(y / (_WIDTH / 5)), 4)
    return _LANES[idx]


def zone_label(x: float | None, y: float | None) -> str:
    """'final third, left half-space' — or '' if either coordinate is missing."""
    if x is None or y is None:
        return ""
    return f"{third(x)}, {lane(y)}"
