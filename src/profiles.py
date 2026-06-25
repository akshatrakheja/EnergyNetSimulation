"""
profiles.py — Load and PV profile generation.

All profiles are returned as pandas DataFrames with a DatetimeIndex and
columns named by node ID (e.g. 'H1', 'H2', 'H3', 'S').

Units: kW (instantaneous power at each timestep).

Load shapes — entity-type catalogue
-------------------------------------
Each entity type has a 24-hourly per-unit anchor array normalised to mean≈1.
The `node_type` key in node_cfg selects the shape; defaults to residential.

  residential   — Indian semi-urban household (bimodal: morning + evening)
  shop          — Kirana/retail: high 9am–9pm, near-zero at night
  cold_storage  — Flat compressor load, ~25% higher when ambient is hot (daytime)
  telecom_tower — 24/7 near-flat, slight AC variation with ambient temperature
  school        — Daytime-only 8am–3pm; CCTV/security ~5% at night
  pump          — Irrigation: two 2-hour sessions at dawn (5–7am) + dusk (5–7pm)
  streetlight   — Off during daylight; full 6–10pm; dimmed 10pm–5am
  phc           — Primary Health Centre: 24/7 base + full operation 9am–5pm
  apartment     — Urban flat: similar to residential but smaller morning peak

Sources and verification:
  residential   : Prayas eMARC 2021 smart-meter data (Maharashtra+UP, 115 hh)
  shop          : Qbits Energy solar-for-shop guide 2024 + BESCOM/UCB DR study 2022
  cold_storage  : Sameeeksha Hooghly Cold Storage Cluster Profile (BEE, 2018)
                  + Rajkot 40-ton case study (Qbits 2024, 24-hr demand logging)
  telecom_tower : TRAI/TelecomLead white paper (2013) + GSMA BESS study (2024)
                  avg 2.52 kW for 3-BTS site; AC adds ~15% midday
  school        : Qbits solar-for-school guide 2024; Coimbatore CBSE 25 kW case
                  study (measured peak 21 kW, 233 school-days/yr)
  pump          : IJIET agricultural DSM study (2017, 1337 pumpsets, Karnataka);
                  IRJET 11kV agri-feeder analysis (2024, Rabi peak 10am–2pm)
  streetlight   : Standard DISCOM practice; LED dimming 50% after 10pm
  phc           : MNRE rural health facility electrification guidelines (2019)
  apartment     : Derived from residential with reduced morning cooking peak
                  (induction cooker less dominant in flats with LPG availability)

PV shape — standard half-sine, India sunrise 06:30–19:30 (June solstice)
-------------------------------------------------------------------------
Multi-day cloud variability: each day gets an independent irradiance scale
drawn from a weather sequence (see `generate_weather_sequence`).

Interface
---------
load_profiles(market, node_cfg, dt_min, horizon_h, seed) -> DataFrame
pv_profiles(node_cfg, dt_min, horizon_h, irradiance_scale,
            daily_irradiance_scales, seed)  -> DataFrame
generate_weather_sequence(n_days, season, seed) -> list[float]
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Indian residential load shape (normalised pu, 24 hourly anchors)
# ---------------------------------------------------------------------------
# Each entry is the per-unit load at that hour (hour 0 = midnight).
# The vector is normalised so its mean ≈ 1.0 (done at runtime).
# This is calibrated for a semi-urban household with:
#   - Induction cooker  (1.0–1.5 kW, morning + evening)
#   - Ceiling fans      (0.075 kW × 3)
#   - Refrigerator      (0.08 kW steady)
#   - LED lighting      (0.05–0.10 kW)
#   - TV                (0.08 kW, evening)
#   - No AC (AC variant multiplies 15:00–22:00 by 1.4–2.0)
_INDIA_LOAD_PU_HOURLY = np.array([
    0.20,   #  0 AM — night base: fans + fridge + standby
    0.18,   #  1 AM — lowest point
    0.17,   #  2 AM
    0.17,   #  3 AM
    0.19,   #  4 AM
    0.24,   #  5 AM — first light, some activity
    0.40,   #  6 AM — morning ramp: lights, water pump
    0.68,   #  7 AM — breakfast cooking (induction + geyser)
    0.84,   #  8 AM — peak morning: induction + geyser + fan
    0.72,   #  9 AM — geyser off, fans running
    0.38,   # 10 AM — midday trough starts (people leave)
    0.27,   # 11 AM — trough: fridge + fans only
    0.25,   # 12 PM — deepest trough
    0.26,   #  1 PM
    0.33,   #  2 PM — afternoon ramp: cooler/AC begins
    0.44,   #  3 PM — hottest hour, cooler/AC on
    0.50,   #  4 PM — sustained afternoon cooling load
    0.54,   #  5 PM — people return home, TV on
    0.66,   #  6 PM — evening cooking prep, lights on
    0.86,   #  7 PM — dinner cooking: induction + lights + TV + fan
    0.96,   #  8 PM — near-peak: all appliances
    1.00,   #  9 PM — absolute evening peak
    0.86,   # 10 PM — post-dinner: AC/fan + TV
    0.62,   # 11 PM — winding down
], dtype=float)

assert len(_INDIA_LOAD_PU_HOURLY) == 24, "Must have exactly 24 hourly anchors"


# ---------------------------------------------------------------------------
# Additional entity-type load shapes (24 hourly anchors, raw — NOT normalised)
# Normalisation (mean→1) is applied at interpolation time.
# ---------------------------------------------------------------------------

# Kirana shop / retail: closed at night (fridge + security light ≈ 5%),
# opens ~9am, peak ~11am–2pm (AC + customers), closes ~9pm.
# Source: Qbits Energy solar-for-shop 2024; BESCOM/UCB DR study 2022.
_SHOP_LOAD_PU_HOURLY = np.array([
    0.05, 0.04, 0.04, 0.04, 0.04, 0.06,   #  0– 5 AM  closed, fridge + security light
    0.10, 0.15, 0.25, 0.55, 0.80, 0.95,   #  6–11 AM  opening ramp, morning rush
    1.00, 0.95, 0.90, 0.88, 0.90, 0.92,   # 12– 5 PM  peak activity, midday AC
    0.95, 0.98, 0.85, 0.65, 0.35, 0.08,   #  6–11 PM  evening trade, close ≈9–10pm
], dtype=float)

# Agricultural cold storage (small, 10–50 ton capacity).
# Compressor duty cycle ≈60–80%; daytime ambient heat raises load ~25% vs night.
# Load factor 80–90%.  Nearly flat but with a clear diurnal bulge.
# Source: Sameeeksha/BEE Hooghly cluster (2018); Rajkot 40-ton case (Qbits 2024).
_COLD_STORAGE_LOAD_PU_HOURLY = np.array([
    0.78, 0.76, 0.75, 0.74, 0.74, 0.76,   #  0– 5 AM  nighttime minimum
    0.80, 0.83, 0.87, 0.92, 0.97, 1.00,   #  6–11 AM  ambient rises
    1.00, 1.00, 0.98, 0.96, 0.93, 0.90,   # 12– 5 PM  peak cooling (hottest hours)
    0.88, 0.86, 0.85, 0.84, 0.82, 0.80,   #  6–11 PM  ambient cooling
], dtype=float)

# Telecom tower (3-BTS outdoor site, rural India).
# Near-flat 24/7; slight midday rise from shelter AC; traffic also peaks noon–8pm.
# Avg load ≈2.52 kW; we model as pu relative to peak ≈3 kW.
# Source: TRAI/TelecomLead white paper 2013; GSMA BESS study 2024.
_TELECOM_LOAD_PU_HOURLY = np.array([
    0.90, 0.88, 0.87, 0.87, 0.88, 0.90,   #  0– 5 AM  low traffic, cooler ambient
    0.92, 0.93, 0.95, 0.97, 0.99, 1.00,   #  6–11 AM  traffic + ambient rise
    1.00, 1.00, 1.00, 0.99, 0.99, 0.99,   # 12– 5 PM  peak traffic + peak heat
    0.98, 0.97, 0.96, 0.95, 0.93, 0.91,   #  6–11 PM  evening traffic, ambient drops
], dtype=float)

# Government school (primary/secondary, India).
# CCTV/network runs 24/7 at ~5% base; full load only during school hours.
# Peak: classrooms + labs 9am–1pm.  Water pump 3 hrs/day (absorbed into avg).
# Source: Qbits solar-for-school 2024; Coimbatore CBSE 25 kW case study (Qbits 2024).
_SCHOOL_LOAD_PU_HOURLY = np.array([
    0.04, 0.03, 0.03, 0.03, 0.03, 0.04,   #  0– 5 AM  CCTV + security only
    0.05, 0.18, 0.72, 0.96, 1.00, 0.98,   #  6–11 AM  staff, assembly, morning classes
    0.95, 0.92, 0.85, 0.55, 0.35, 0.12,   # 12– 5 PM  afternoon classes, wind-down
    0.05, 0.04, 0.04, 0.04, 0.04, 0.04,   #  6–11 PM  CCTV + security only
], dtype=float)

# Irrigation borewell pump (5 HP submersible motor ≈ 3.7 kW).
# Two sessions per day: dawn irrigation (5–7am) and dusk irrigation (5–7pm).
# Off between sessions.  DISCOM often restricts to specific windows.
# Source: IJIET agri-DSM Karnataka 2017; IRJET 11kV agri-feeder Rabi season 2024.
_PUMP_LOAD_PU_HOURLY = np.array([
    0.00, 0.00, 0.00, 0.00, 0.00, 1.00,   #  0– 5 AM  off; 5am: dawn session ON
    1.00, 0.00, 0.00, 0.00, 0.00, 0.00,   #  6 AM on; 7am: off until evening
    0.00, 0.00, 0.00, 0.00, 0.00, 1.00,   # 12–4 PM off; 5pm: dusk session ON
    1.00, 0.00, 0.00, 0.00, 0.00, 0.00,   #  6 PM on; 7pm: off for the night
], dtype=float)

# LED street lighting (village / peri-urban road).
# Off during daylight; full brightness 6pm–10pm; 50% dim thereafter until 5am.
# Source: standard DISCOM practice; smart-LED dimming schedules (BEE, 2021).
_STREETLIGHT_LOAD_PU_HOURLY = np.array([
    0.50, 0.50, 0.50, 0.50, 0.50, 0.25,   #  0– 5 AM  dimmed (late-night dim)
    0.00, 0.00, 0.00, 0.00, 0.00, 0.00,   #  6–11 AM  off (daylight)
    0.00, 0.00, 0.00, 0.00, 0.00, 0.00,   # 12– 5 PM  off (daylight)
    1.00, 1.00, 1.00, 1.00, 0.50, 0.50,   #  6– 9 PM  full; 10–11 PM dim
], dtype=float)

# Primary Health Centre (PHC) / rural clinic.
# 24/7 base ≈20% (vaccine fridge + emergency lighting); full OPD 9am–5pm.
# Evening emergency availability ≈40%.
# Non-sheddable fraction: vaccine fridge ≈0.2 kW constant (see critical_load_kw).
# Source: MNRE rural health facility electrification guidelines 2019.
_PHC_LOAD_PU_HOURLY = np.array([
    0.20, 0.18, 0.17, 0.17, 0.18, 0.20,   #  0– 5 AM  fridge + security light
    0.25, 0.35, 0.65, 0.88, 1.00, 1.00,   #  6–11 AM  staff, OPD opens, equipment
    1.00, 0.95, 0.90, 0.88, 0.82, 0.55,   # 12– 5 PM  full operation, afternoon
    0.40, 0.38, 0.30, 0.25, 0.22, 0.20,   #  6–11 PM  evening walk-in, wind-down
], dtype=float)

# Urban apartment unit (multi-storey, shared services).
# Similar to residential but reduced morning cooking peak (more LPG use)
# and stronger evening peak from AC and charging loads.
# Source: derived from residential shape with calibration to BESCOM apartment
# feeder data (BESCOM/UCB 2022 Fig. 6 "multi-family residential").
_APARTMENT_LOAD_PU_HOURLY = np.array([
    0.22, 0.18, 0.16, 0.16, 0.18, 0.22,   #  0– 5 AM  night base
    0.35, 0.52, 0.65, 0.50, 0.30, 0.26,   #  6–11 AM  morning ramp (smaller cooking peak)
    0.28, 0.30, 0.38, 0.48, 0.56, 0.62,   # 12– 5 PM  afternoon build-up
    0.72, 0.88, 1.00, 1.00, 0.88, 0.65,   #  6–11 PM  evening peak (AC + cooking + TV)
], dtype=float)

# ---------------------------------------------------------------------------
# Shape catalogue: node_type key → hourly anchor array
# ---------------------------------------------------------------------------

LOAD_SHAPE_CATALOGUE: dict[str, np.ndarray] = {
    "residential":   _INDIA_LOAD_PU_HOURLY,
    "shop":          _SHOP_LOAD_PU_HOURLY,
    "cold_storage":  _COLD_STORAGE_LOAD_PU_HOURLY,
    "telecom_tower": _TELECOM_LOAD_PU_HOURLY,
    "school":        _SCHOOL_LOAD_PU_HOURLY,
    "pump":          _PUMP_LOAD_PU_HOURLY,
    "streetlight":   _STREETLIGHT_LOAD_PU_HOURLY,
    "phc":           _PHC_LOAD_PU_HOURLY,
    "apartment":     _APARTMENT_LOAD_PU_HOURLY,
}

for _k, _v in LOAD_SHAPE_CATALOGUE.items():
    assert len(_v) == 24, f"LOAD_SHAPE_CATALOGUE['{_k}'] must have 24 hourly anchors"


def _interpolate_shape(shape_24h: np.ndarray, steps_per_day: int) -> np.ndarray:
    """Resample any 24-anchor load curve to `steps_per_day` sub-hourly points.

    Uses PCHIP (shape-preserving cubic) interpolation with periodic wrap-around.
    The result is normalised so its mean == 1.0 (or left as-is if all-zero).
    """
    src_hours = np.arange(24, dtype=float)
    dst_hours = np.linspace(0, 24, steps_per_day, endpoint=False)

    src_ext = np.concatenate([shape_24h[-3:], shape_24h, shape_24h[:3]])
    src_hours_ext = np.concatenate([src_hours[-3:] - 24.0,
                                    src_hours,
                                    src_hours[:3] + 24.0])

    from scipy.interpolate import PchipInterpolator
    interp = PchipInterpolator(src_hours_ext, src_ext)
    shape = interp(dst_hours)
    shape = np.clip(shape, 0.0, None)
    mean_val = shape.mean()
    if mean_val < 1e-9:
        return shape
    return shape / mean_val


def _interpolate_india_shape(steps_per_day: int) -> np.ndarray:
    """Legacy alias — kept for backward compatibility."""
    return _interpolate_shape(_INDIA_LOAD_PU_HOURLY, steps_per_day)


# ---------------------------------------------------------------------------
# PV shape helpers
# ---------------------------------------------------------------------------

def _pv_day_profile(
    steps_per_day: int,
    dt_h: float,
    irradiance_scale: float,
    rng: np.random.Generator,
    sunrise_h: float = 6.5,
    sunset_h: float = 19.5,
) -> np.ndarray:
    """Half-sine PV curve for one day with cloud noise.

    Returns per-unit generation (0–1) × irradiance_scale.
    """
    t = np.linspace(0, 24, steps_per_day, endpoint=False)
    daylight = np.zeros(steps_per_day)
    mask = (t >= sunrise_h) & (t < sunset_h)
    daylight[mask] = np.sin(
        np.pi * (t[mask] - sunrise_h) / (sunset_h - sunrise_h)
    )

    # Fast cloud turbulence (6% std, per-step independent)
    cloud_fast = 1.0 + 0.06 * rng.standard_normal(steps_per_day)
    # Slow cloud drift (10% drift, correlated across the day)
    drift = irradiance_scale * (
        1.0 + 0.10 * rng.standard_normal(steps_per_day).cumsum()
              / np.sqrt(steps_per_day)
    )
    cloud = np.clip(drift * cloud_fast, 0.0, 1.0)
    return np.clip(daylight * cloud, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Multi-day load and PV generators
# ---------------------------------------------------------------------------

def _build_load_profile(
    n_steps: int,
    avg_kw: float,
    dt_h: float,
    rng: np.random.Generator,
    day_variation: float = 0.08,
    node_type: str = "residential",
) -> np.ndarray:
    """Entity-type-aware load profile tiled across n_steps.

    Parameters
    ----------
    n_steps : int
        Total number of timesteps.
    avg_kw : float
        Target mean power (kW).
    dt_h : float
        Timestep duration (hours).
    rng : np.random.Generator
    day_variation : float
        Fraction std-dev of per-day scaling (default 8%).  Set lower (e.g. 0.03)
        for highly regular loads like telecom towers or cold storage.
    node_type : str
        Key into LOAD_SHAPE_CATALOGUE.  Unknown types fall back to 'residential'.
    """
    steps_per_day = round(24.0 / dt_h)
    anchor = LOAD_SHAPE_CATALOGUE.get(node_type, _INDIA_LOAD_PU_HOURLY)
    shape = _interpolate_shape(anchor, steps_per_day)   # mean == 1.0 (or 0 if all-zero)

    n_days = int(np.ceil(n_steps / steps_per_day))
    segments = []
    for _ in range(n_days):
        daily_scale = np.exp(rng.normal(0.0, day_variation))
        day_noise = 1.0 + 0.04 * rng.standard_normal(steps_per_day)
        seg = shape * daily_scale * day_noise
        seg = np.clip(seg, 0.0, None)
        segments.append(seg)

    profile = np.concatenate(segments)[:n_steps]
    profile_mean = profile.mean()
    if profile_mean > 1e-9:
        profile = profile / profile_mean * avg_kw
    return np.clip(profile, 0.0, None)


def _build_pv_profile(
    n_steps: int,
    peak_kw: float,
    dt_h: float,
    rng: np.random.Generator,
    daily_scales: list[float] | None = None,
    irradiance_scale: float = 1.0,
    sunrise_h: float = 6.5,
    sunset_h: float = 19.5,
) -> np.ndarray:
    """PV generation profile for one or more days.

    Parameters
    ----------
    daily_scales : list[float] | None
        Per-day irradiance scales; if None, uses `irradiance_scale` for all days.
    irradiance_scale : float
        Fallback global scale (used when daily_scales is None).
    """
    steps_per_day = round(24.0 / dt_h)
    n_days = int(np.ceil(n_steps / steps_per_day))

    if daily_scales is None:
        daily_scales = [irradiance_scale] * n_days
    else:
        # Pad if needed
        while len(daily_scales) < n_days:
            daily_scales = list(daily_scales) + [irradiance_scale]

    segments = []
    for d in range(n_days):
        seg = _pv_day_profile(
            steps_per_day, dt_h,
            irradiance_scale=daily_scales[d],
            rng=rng,
            sunrise_h=sunrise_h,
            sunset_h=sunset_h,
        )
        segments.append(seg)

    profile = np.concatenate(segments)[:n_steps]
    return profile * peak_kw


# ---------------------------------------------------------------------------
# Weather sequence generator
# ---------------------------------------------------------------------------

# Typical summer-monsoon week irradiance patterns for north/central India.
# Values are daily irradiance_scale (0–1).
_WEATHER_TEMPLATES: dict[str, list[float]] = {
    # June solstice week: mostly sunny, afternoon build-up
    "summer_sunny":    [1.00, 0.95, 0.90, 0.85, 1.00, 0.92, 0.88],
    # Monsoon onset (July): alternating sun and heavy cloud
    "monsoon":         [0.65, 0.30, 0.80, 0.25, 0.70, 0.35, 0.60],
    # Post-monsoon (October): high clarity, excellent solar
    "post_monsoon":    [0.98, 0.95, 1.00, 0.97, 0.90, 0.95, 1.00],
    # Winter (December): clear but low elevation, 35% irradiance equivalent
    "winter":          [0.35, 0.38, 0.32, 0.36, 0.40, 0.35, 0.33],
    # Overcast / haze (pre-monsoon dust storms)
    "hazy":            [0.40, 0.50, 0.30, 0.45, 0.55, 0.35, 0.45],
    # Mix: 3 sunny + 1 cloudy + 3 sunny (typical simulation week)
    "mixed_week":      [1.00, 0.95, 0.90, 0.25, 0.85, 0.98, 1.00],
}


def generate_weather_sequence(
    n_days: int,
    season: str = "summer_sunny",
    seed: int | None = None,
    jitter: float = 0.05,
) -> list[float]:
    """Generate a per-day irradiance_scale sequence.

    Parameters
    ----------
    n_days : int
        Number of days to generate.
    season : str
        Key into `_WEATHER_TEMPLATES`.  Unknown keys fall back to 'summer_sunny'.
    seed : int | None
        RNG seed.
    jitter : float
        Gaussian std-dev applied to template values (adds day-to-day variation).

    Returns
    -------
    list[float]
        Irradiance scales, one per day, in [0, 1].
    """
    template = _WEATHER_TEMPLATES.get(season, _WEATHER_TEMPLATES["summer_sunny"])
    rng = np.random.default_rng(seed)

    n_template = len(template)
    # Tile template, add jitter
    scales: list[float] = []
    for i in range(n_days):
        base = template[i % n_template]
        val = float(np.clip(base + rng.normal(0.0, jitter), 0.05, 1.0))
        scales.append(val)
    return scales


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _make_index(dt_min: int, horizon_h: float, start: str = "2024-06-21") -> pd.DatetimeIndex:
    freq = f"{dt_min}min"
    n = int(np.ceil(horizon_h * 60 / dt_min))
    return pd.date_range(start=start, periods=n, freq=freq)


def load_profiles(
    market: str,
    node_cfg: dict[str, dict[str, Any]],
    dt_min: int = 15,
    horizon_h: float = 24.0,
    seed: int = 42,
    start: str = "2024-06-21",
    day_variation: float = 0.08,
) -> pd.DataFrame:
    """Return a DataFrame of load profiles (kW) for each node.

    Parameters
    ----------
    market : str
        'india_semi_urban' | 'synthetic' (alias).
        Other values fall back to the Indian shape with a warning.
    node_cfg : dict
        {node_id: {'avg_load_kw': float, ...}, ...}
        Nodes with no 'avg_load_kw' key are skipped.
    dt_min : int
        Timestep in minutes.
    horizon_h : float
        Simulation horizon (can be > 24 for multi-day runs).
    seed : int
        RNG seed.
    start : str
        ISO date for index start.
    day_variation : float
        Per-day scaling std-dev (8% default).  Increase for more variability.
    """
    if market not in ("india_semi_urban", "synthetic"):
        warnings.warn(
            f"Real data loader for market='{market}' is not yet implemented. "
            "Using Indian semi-urban shape.",
            stacklevel=2,
        )

    idx = _make_index(dt_min, horizon_h, start)
    n = len(idx)
    dt_h = dt_min / 60.0
    rng = np.random.default_rng(seed)

    data: dict[str, np.ndarray] = {}
    for node_id, cfg in node_cfg.items():
        avg = cfg.get("avg_load_kw", None)
        if avg is None:
            continue
        ntype = cfg.get("node_type", "residential")
        if ntype not in LOAD_SHAPE_CATALOGUE:
            warnings.warn(
                f"Node '{node_id}': unknown node_type='{ntype}', "
                "falling back to 'residential'.",
                stacklevel=2,
            )
            ntype = "residential"
        data[node_id] = _build_load_profile(
            n, avg, dt_h, rng, day_variation, node_type=ntype
        )

    return pd.DataFrame(data, index=idx)


def pv_profiles(
    node_cfg: dict[str, dict[str, Any]],
    dt_min: int = 15,
    horizon_h: float = 24.0,
    irradiance_scale: float = 1.0,
    daily_irradiance_scales: list[float] | None = None,
    seed: int = 42,
    start: str = "2024-06-21",
    pv_fraction: float = 1.0,
    sunrise_h: float = 6.5,
    sunset_h: float = 19.5,
) -> pd.DataFrame:
    """Return a DataFrame of PV generation profiles (kW) for each node.

    Parameters
    ----------
    node_cfg : dict
        {node_id: {'pv_kw': float, ...}, ...}
    irradiance_scale : float
        Global scale (0–1), used for all days when daily_irradiance_scales is None.
    daily_irradiance_scales : list[float] | None
        Per-day irradiance scales.  Overrides `irradiance_scale` when provided.
        Length should equal ceiling(horizon_h / 24).  Auto-padded if shorter.
    pv_fraction : float
        Fraction of panels operational (S1 scenario: 0.5).
    sunrise_h, sunset_h : float
        Daylight window in decimal hours (default: 06:30–19:30 for June India).
    """
    idx = _make_index(dt_min, horizon_h, start)
    n = len(idx)
    dt_h = dt_min / 60.0
    rng = np.random.default_rng(seed + 1000)

    data: dict[str, np.ndarray] = {}
    for node_id, cfg in node_cfg.items():
        peak = cfg.get("pv_kw", None)
        if peak is None:
            continue
        effective_peak = peak * pv_fraction
        data[node_id] = _build_pv_profile(
            n, effective_peak, dt_h, rng,
            daily_scales=daily_irradiance_scales,
            irradiance_scale=irradiance_scale,
            sunrise_h=sunrise_h,
            sunset_h=sunset_h,
        )

    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# Seasonal / scenario helpers
# ---------------------------------------------------------------------------

SEASONAL_PARAMS: dict[str, dict] = {
    "summer":      {"irradiance_scale": 1.00, "start": "2024-06-21",
                    "weather": "summer_sunny"},
    "winter":      {"irradiance_scale": 0.35, "start": "2024-01-15",
                    "weather": "winter"},
    "cloudy":      {"irradiance_scale": 0.25, "start": "2024-04-10",
                    "weather": "hazy"},
    "monsoon":     {"irradiance_scale": 0.55, "start": "2024-07-15",
                    "weather": "monsoon"},
    "post_monsoon":{"irradiance_scale": 0.97, "start": "2024-10-15",
                    "weather": "post_monsoon"},
    "mixed_week":  {"irradiance_scale": 0.85, "start": "2024-06-21",
                    "weather": "mixed_week"},
}
