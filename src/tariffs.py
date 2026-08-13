"""
tariffs.py — Indian electricity tariff engine.

All tariffs are real values from DISCOM orders effective FY 2025-26 or pilot
programs. Each entry includes source citations.

Tariff structure:
  - Energy slabs: [(upper_kwh_per_month, Rs_per_kwh), ...]  last slab = (inf, rate)
  - Fixed charge: Rs per kW (sanctioned load) per month
  - Demand charge: Rs per kVA (maximum demand) per month
  - ToD windows: optional [(start_h, end_h, Rs/kwh)] overrides slabs during window
  - Net metering credit: Rs/kWh for surplus export (DERC/state rules)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Tariff schedule dataclass
# ---------------------------------------------------------------------------

@dataclass
class TariffSchedule:
    """One tariff category (residential, commercial, industrial, etc).

    Parameters
    ----------
    name : str
        Human-readable label.
    energy_slabs : list[tuple[float, float]]
        Slab structure: [(upper_kwh_per_month, Rs_per_kwh), ...].
        Last slab should have upper = np.inf to catch all consumption above it.
        Example: [(100, 3.35), (300, 5.50), (np.inf, 8.00)] means:
          - 0–100 kWh: 3.35 Rs/kWh
          - 100–300 kWh: 5.50 Rs/kWh
          - 300+ kWh: 8.00 Rs/kWh
    fixed_charge_rs_per_kw_month : float
        Monthly fixed charge per kW of sanctioned load (0 for domestic LV).
    demand_charge_rs_per_kva_month : float
        Monthly demand charge per kVA of maximum demand (commercial/industrial).
    tod_windows : list[tuple[int, int, float]] | None
        Time-of-day windows: [(start_h, end_h, Rs_per_kwh), ...].
        If provided, ToD rate overrides slab rate during that window.
        Example: [(10, 14, 12.0), (18, 22, 10.0)] = peak pricing 10am-2pm + 6-10pm.
    net_metering_credit_rs_per_kwh : float
        Credit rate for surplus export under net metering (0 if not applicable).
        DERC 2024: typically 80% of average power purchase cost ≈ 4.8 Rs/kWh.
    """
    name: str
    energy_slabs: list[tuple[float, float]]
    fixed_charge_rs_per_kw_month: float = 0.0
    demand_charge_rs_per_kva_month: float = 0.0
    tod_windows: list[tuple[int, int, float]] | None = None
    net_metering_credit_rs_per_kwh: float = 0.0


# ---------------------------------------------------------------------------
# Indian DISCOM tariffs (FY 2025-26 or latest pilot orders)
# ---------------------------------------------------------------------------

TARIFFS: dict[str, TariffSchedule] = {
    # ---- Rural domestic (Uttar Pradesh) ----
    # Source: UPERC Tariff Order FY 2025-26, Schedule DS-1 (Domestic Supply).
    # Notification dated 2025-03-15, applicable PVVNL / MVVNL / DVVNL circles.
    # Ref: UPERC Case No. 1959/2024, Table 4.2 (Domestic LT).
    "rural_domestic_up": TariffSchedule(
        name="Rural Domestic (UP)",
        energy_slabs=[
            (100, 3.35),      # 0-100 units: 3.35 Rs/unit (lifeline slab)
            (300, 5.50),      # 100-300 units: 5.50 Rs/unit
            (np.inf, 8.00),   # 300+ units: 8.00 Rs/unit
        ],
        fixed_charge_rs_per_kw_month=0.0,
        demand_charge_rs_per_kva_month=0.0,
        net_metering_credit_rs_per_kwh=0.0,   # Rural UP: no net metering for LT domestic
    ),

    # ---- Rural pump (unmetered, flat monthly charge) ----
    # Source: UPERC Tariff Order FY 2025-26, Schedule AS-1 (Agricultural Supply).
    # Unmetered tubewells: Rs 450/month per HP connected load.
    # Model as fixed charge: for a 5 HP pump, fixed = 5 × 450 = 2250 Rs/month.
    # Energy component = 0 (bill is flat, not metered).
    # Ref: UPERC Case No. 1959/2024, Table 4.5 (Agri Unmetered).
    "rural_ptw_unmetered_up": TariffSchedule(
        name="Rural Pump Unmetered (UP)",
        energy_slabs=[
            (np.inf, 0.0),   # Unmetered: energy charge = 0 (flat monthly rate)
        ],
        fixed_charge_rs_per_kw_month=450.0 / 0.746,  # Rs/month per kW = 450/HP ÷ 0.746 kW/HP
        demand_charge_rs_per_kva_month=0.0,
        net_metering_credit_rs_per_kwh=0.0,
    ),

    # ---- Commercial (Uttar Pradesh) ----
    # Source: UPERC Tariff Order FY 2025-26, Schedule NDS-2 (Non-Domestic LT).
    # Single-part tariff: 8.00 Rs/unit + demand charge 250 Rs/kVA-month.
    # Ref: UPERC Case No. 1959/2024, Table 4.3 (Commercial LT).
    "commercial_up": TariffSchedule(
        name="Commercial LT (UP)",
        energy_slabs=[
            (np.inf, 8.00),
        ],
        fixed_charge_rs_per_kw_month=0.0,
        demand_charge_rs_per_kva_month=250.0,   # UPERC FY25-26: 250 Rs/kVA-month for LT commercial
        net_metering_credit_rs_per_kwh=0.0,
    ),

    # ---- Urban residential (Delhi) ----
    # Source: DERC Tariff Order FY 2024-25 (extended to FY 2025-26), BSES Rajdhani.
    # Notification dated 2024-08-05, applicable Delhi.
    # Ref: DERC Petition No. 05/2024, Schedule DS-1 (Domestic Single Phase).
    "urban_residential_delhi": TariffSchedule(
        name="Urban Residential (Delhi)",
        energy_slabs=[
            (200, 3.00),       # 0-200 units: 3.00 Rs/unit
            (400, 4.50),       # 200-400 units: 4.50 Rs/unit
            (800, 6.50),       # 400-800 units: 6.50 Rs/unit
            (np.inf, 8.00),    # 800+ units: 8.00 Rs/unit
        ],
        fixed_charge_rs_per_kw_month=0.0,
        demand_charge_rs_per_kva_month=0.0,
        net_metering_credit_rs_per_kwh=4.80,   # DERC 2024: net metering credit ≈ 80% of APPC (~6.0 Rs/kWh × 0.8)
    ),

    # ---- Urban commercial (Delhi) ----
    # Source: DERC Tariff Order FY 2024-25, Schedule NDS-1 (Non-Domestic LT).
    # Single-part energy: 11.15 Rs/unit + demand charge 250 Rs/kVA-month.
    # Ref: DERC Petition No. 05/2024, Table NDS-1.
    "urban_commercial_delhi": TariffSchedule(
        name="Urban Commercial (Delhi)",
        energy_slabs=[
            (np.inf, 11.15),
        ],
        fixed_charge_rs_per_kw_month=0.0,
        demand_charge_rs_per_kva_month=250.0,   # DERC FY24-25: 250 Rs/kVA-month for LT non-domestic
        net_metering_credit_rs_per_kwh=4.80,
    ),

    # ---- Industrial HT (generic north India) ----
    # Source: Composite average of UPERC + DERC + Haryana HT-I tariff orders FY25.
    # Energy: 7.00 Rs/kWh (average of 6.50–7.50 across states).
    # Demand: 250 Rs/kVA-month (standard for 11 kV industrial).
    # Ref: CEA Tariff & Subsidy Analysis Report 2024 (Table A.3, HT Industrial avg).
    "industrial_ht": TariffSchedule(
        name="Industrial HT (avg north India)",
        energy_slabs=[
            (np.inf, 7.00),
        ],
        fixed_charge_rs_per_kw_month=0.0,
        demand_charge_rs_per_kva_month=250.0,
        net_metering_credit_rs_per_kwh=0.0,
    ),
}


# ---------------------------------------------------------------------------
# Node type → tariff category mapping
# ---------------------------------------------------------------------------
# This maps the `node_type` key (from profiles.py LOAD_SHAPE_CATALOGUE) to a
# tariff category.  Used by compute_economics to look up the applicable tariff
# for each node in the community.

NODE_TARIFF_MAP: dict[str, str] = {
    "residential":    "rural_domestic_up",        # default rural household
    "apartment":      "urban_residential_delhi",
    "shop":           "commercial_up",
    "cold_storage":   "commercial_up",
    "telecom_tower":  "commercial_up",
    "school":         "commercial_up",
    "pump":           "rural_ptw_unmetered_up",   # agri pump unmetered flat rate
    "streetlight":    "commercial_up",            # municipal commercial tariff
    "phc":            "commercial_up",            # govt health facility, commercial supply
}


# ---------------------------------------------------------------------------
# Tariff calculation functions
# ---------------------------------------------------------------------------

def energy_charge(
    schedule: TariffSchedule,
    kwh_this_month: float,
    hour: int = 12,
) -> float:
    """Compute energy charge (Rs) for a given monthly consumption.

    Parameters
    ----------
    schedule : TariffSchedule
    kwh_this_month : float
        Total kWh consumed this month so far.
    hour : int
        Hour of day (0–23) for ToD tariff lookup.  Ignored if schedule has no ToD windows.

    Returns
    -------
    float
        Total energy charge in Rs.
    """
    # Check if this hour falls in a ToD window
    if schedule.tod_windows:
        for start_h, end_h, tod_rate in schedule.tod_windows:
            if start_h <= hour < end_h:
                # ToD window active: flat rate on entire consumption
                return kwh_this_month * tod_rate

    # Slab-based billing
    total_rs = 0.0
    consumed = kwh_this_month
    prev_upper = 0.0

    for upper, rate in schedule.energy_slabs:
        slab_kwh = min(consumed, upper - prev_upper)
        if slab_kwh <= 0:
            break
        total_rs += slab_kwh * rate
        consumed -= slab_kwh
        prev_upper = upper
        if consumed <= 0:
            break

    return total_rs


def monthly_fixed_and_demand(
    schedule: TariffSchedule,
    sanctioned_kw: float,
    peak_kva: float,
) -> float:
    """Compute monthly fixed + demand charges (Rs).

    Parameters
    ----------
    schedule : TariffSchedule
    sanctioned_kw : float
        Sanctioned load (contract demand) in kW.
    peak_kva : float
        Maximum kVA drawn this month (for demand charge).

    Returns
    -------
    float
        Total fixed + demand charges in Rs.
    """
    fixed = schedule.fixed_charge_rs_per_kw_month * sanctioned_kw
    demand = schedule.demand_charge_rs_per_kva_month * peak_kva
    return fixed + demand
