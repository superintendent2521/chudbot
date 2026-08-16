"""Audio filters used by the Lavalink music subsystem."""

from __future__ import annotations

from typing import Any, Dict

from lavalink.filters import Filter


class AudioNormalization(Filter):
    """LavaDSPX peak normalizer exposed through Lavalink plugin filters."""

    def __init__(self, max_amplitude: float, adaptive: bool) -> None:
        super().__init__({}, plugin_filter=True)
        self.update(max_amplitude=max_amplitude, adaptive=adaptive)

    def update(self, **kwargs: Any) -> None:
        """Update normalization values using Lavalink.py's filter contract."""
        max_amplitude = kwargs.get("max_amplitude", self.values.get("maxAmplitude"))
        adaptive = kwargs.get("adaptive", self.values.get("adaptive"))

        if max_amplitude is not None:
            amplitude = float(max_amplitude)
            if not 0.0 <= amplitude <= 1.0:
                raise ValueError("max_amplitude must be between 0.0 and 1.0")
            self.values["maxAmplitude"] = amplitude
        if adaptive is not None:
            if not isinstance(adaptive, bool):
                raise TypeError("adaptive must be a bool")
            self.values["adaptive"] = adaptive

    def serialize(self) -> Dict[str, Any]:
        return {"normalization": dict(self.values)}
