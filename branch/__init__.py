"""branch: shade-aware pedestrian routing from street-tree shadows.

Public API::

    from branch.config import AREAS
    from branch.pipeline import analyze

    out = analyze(AREAS["park_slope"], "2026-07-15 15:00", alpha=4.0,
                  from_latlon=(40.6772, -73.9735), to_latlon=(40.6675, -73.9855))
    print(out["result"]["fast"]["sun_frac"], out["result"]["cool"]["sun_frac"])
"""
from __future__ import annotations

__version__ = "0.1.0"

from .pipeline import analyze  # noqa: E402,F401
