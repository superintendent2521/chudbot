"""Audio filters used by the Lavalink music subsystem."""

from __future__ import annotations

from typing import Any, Dict

from lavalink.filters import Filter


class AudioNormalization(Filter):
    """LavaDSPX peak normalizer exposed through Lavalink plugin filters."""

    def __init__(self, max_amplitude: float, adaptive: bool) -> None:
        super().__init__()
        self.max_amplitude = max_amplitude
        self.adaptive = adaptive

    def serialize(self) -> Dict[str, Any]:
        return {
            "pluginFilters": {
                "normalization": {
                    "maxAmplitude": self.max_amplitude,
                    "adaptive": self.adaptive,
                }
            }
        }
