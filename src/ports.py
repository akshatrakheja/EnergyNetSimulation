"""
ports.py — Phase 1 datasheet: η(P) interpolators, P_max, P_standby.

Each port is a converter characterized on a 5 kW / 400 V DC basis.
Data source: Stage 7 sweep (BuckBoostSyncLossy, LlcClosedLoopLossy,
DabClosedLoopLossy, FullStackPortLossy).

Sign convention: P is always the *output* power of the port (≥ 0).
For bidirectional ports the same η curve applies in both directions
(characterization was symmetric within measurement noise).
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Raw sweep data  (W → dimensionless efficiency)
# ---------------------------------------------------------------------------

_PEER_RAW: dict[float, float] = {
    250: 0.8666, 500: 0.9284, 750: 0.9510, 1000: 0.9627, 1500: 0.9747,
    2000: 0.9807, 2500: 0.9843, 3000: 0.9865, 3500: 0.9880, 4000: 0.9889,
    4500: 0.9894, 4750: 0.98944, 5000: 0.98939,
}

_BATTERY_RAW: dict[float, float] = {
    250: 0.9755, 500: 0.9786, 750: 0.9794, 1000: 0.9796, 1250: 0.9796,
    1500: 0.9795, 1750: 0.9793, 2000: 0.9791, 2250: 0.9788, 2500: 0.9785,
    2750: 0.9782, 3000: 0.9779, 3250: 0.9776, 3500: 0.9773, 3750: 0.9769,
    4000: 0.9766, 4250: 0.9763, 4500: 0.9759, 4750: 0.9756, 5000: 0.9753,
}

_SOLAR_RAW: dict[float, float] = {
    250: 0.8088, 500: 0.8895, 750: 0.9194, 1000: 0.9358, 1250: 0.9460,
    1500: 0.9526, 1750: 0.9580, 2000: 0.9617, 2250: 0.9646, 2500: 0.9666,
    2750: 0.9687, 3000: 0.9701, 3250: 0.9715, 3500: 0.9723, 3750: 0.9730,
    4000: 0.9738, 4250: 0.9741, 4500: 0.9746, 4750: 0.9751, 5000: 0.9755,
}

_GRID_RAW: dict[float, float] = {
    500: 0.8649, 1000: 0.9136, 1500: 0.9287, 2000: 0.9346, 2500: 0.9368,
    3000: 0.9369, 3500: 0.9357, 4000: 0.9336, 4500: 0.9322, 5000: 0.9297,
}

# Standby draw (W) — power burned just to keep a port energised, at zero throughput.
# Battery standby is an optimistic lower bound from the Phase 1 model (misses gate-drive
# / core floor); treat as tunable.
P_STANDBY_W: dict[str, float] = {
    "peer":    38.4,
    "battery":  3.9,   # ⚠ lower bound; real range ≈ 3–30 W (gate-drive/core floor not
                       #   modelled in BuckBoostSyncLossy — only current-scaling losses)
    "solar":   42.0,
    "grid":    57.9,
}

# ---------------------------------------------------------------------------
# TODO: Pessimistic hardware bound
# ---------------------------------------------------------------------------
# To bracket uncertainty against real hardware, run a sensitivity sweep with:
#   P_STANDBY_W["battery"] = 20.0   (gate-drive floor ~15–25 W for 100 kHz converter)
#   η curves degraded uniformly by 2 pp (temperature derating, ~50°C ambient India)
#   grid port delivery deficit: ~7% at full load (PR controller tracking residual)
# Expected impact on S0: port losses +6–14 kWh/day, grid import +5–12 kWh,
# self-sufficiency drops ~4–8 pp. Curtailment unchanged (grid-cap driven, not η driven).
# See build log Stage 7 lessons + Stage 5d-Lossy open items for root causes.
# ---------------------------------------------------------------------------

P_MAX_W: dict[str, float] = {
    "peer":    5000.0,
    "battery": 5000.0,
    "solar":   5000.0,
    "grid":    5000.0,
}

# ---------------------------------------------------------------------------
# Internal: build sorted arrays once at module load
# ---------------------------------------------------------------------------

def _build_arrays(raw: dict[float, float]) -> tuple[np.ndarray, np.ndarray]:
    pts = sorted(raw.items())
    return np.array([p for p, _ in pts]), np.array([e for _, e in pts])


_CURVES: dict[str, tuple[np.ndarray, np.ndarray]] = {
    "peer":    _build_arrays(_PEER_RAW),
    "battery": _build_arrays(_BATTERY_RAW),
    "solar":   _build_arrays(_SOLAR_RAW),
    "grid":    _build_arrays(_GRID_RAW),
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def eta(port: str, p_out_w: float) -> float:
    """Return η for *port* at output power *p_out_w* (watts, ≥ 0).

    Below the smallest tabulated point (where standby dominates), efficiency
    is modelled as  p_out / (p_out + P_standby)  rather than extrapolating
    the interpolation curve off a cliff.

    Above P_max the call is clamped — callers should enforce P_max themselves
    and never reach this branch in normal operation.
    """
    if port not in _CURVES:
        raise ValueError(f"Unknown port '{port}'. Valid: {list(_CURVES)}")
    p_out_w = float(p_out_w)
    if p_out_w <= 0.0:
        return 0.0

    p_arr, e_arr = _CURVES[port]
    p_out_w = min(p_out_w, P_MAX_W[port])

    if p_out_w < p_arr[0]:
        # Below lowest sample: standby-dominated region
        standby = P_STANDBY_W[port]
        return p_out_w / (p_out_w + standby) if (p_out_w + standby) > 0 else 0.0

    return float(np.interp(p_out_w, p_arr, e_arr))


def input_power_w(port: str, p_out_w: float) -> float:
    """Return the input power required to deliver *p_out_w* through *port*.

    input = p_out / η(p_out) + P_standby
    Standby is always added when a port is energised, even at zero throughput.
    """
    if p_out_w <= 0.0:
        return P_STANDBY_W.get(port, 0.0)
    e = eta(port, p_out_w)
    if e <= 0.0:
        return float("inf")
    return p_out_w / e + P_STANDBY_W[port]


def loss_w(port: str, p_out_w: float) -> float:
    """Conversion loss + standby for *port* delivering *p_out_w*."""
    return input_power_w(port, p_out_w) - p_out_w


def port_names() -> list[str]:
    return list(_CURVES.keys())
