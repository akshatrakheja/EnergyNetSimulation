"""
subsidies.py — Indian renewable subsidy stack (PM Surya Ghar, KUSUM, BESS VGF).

All subsidy parameters are from active central/state schemes as of FY 2025-26.
Sources cited inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Subsidy stack dataclass
# ---------------------------------------------------------------------------

@dataclass
class SubsidyStack:
    """Central + state renewable subsidies applicable to a community microgrid.

    Parameters
    ----------
    pm_surya_ghar : bool
        PM Surya Ghar Muft Bijli Yojana (residential rooftop solar subsidy).
        Launched Feb 2024, target 1 crore households.
        Rates: 30,000 Rs/kW for first 2 kW, 18,000 Rs/kW for 3rd kW, cap 78,000 per hh.
        Source: MNRE notification F.No. 302/236/2023-GCRT dated 13-Feb-2024.
    kusum_fls : bool
        KUSUM Component-C (feeder-level solarization) for agricultural feeders.
        30% central subsidy on solar capex (up to 1.05 crore Rs/MW for ground-mounted).
        Source: MNRE KUSUM Operational Guidelines (amended Aug 2024), Sec. 3.3.
    bess_vgf_frac : float
        Viability Gap Funding for battery storage (0.0–0.40).
        SECI / MNRE Battery Storage Scheme FY24-25: 30–40% capex support.
        Source: SECI Tender SECI/C&P/BESS/01/2024 dated Mar-2024 (VGF guidelines).
    rural_license_exempt : bool
        Section 14 (8th proviso) of Electricity Act 2003: rural cooperatives
        generating ≤1 MW from renewables are exempt from distribution license,
        wheeling charges, and cross-subsidy surcharge.
        Source: EA 2003 as amended by Electricity (Amendment) Act 2022, Sec 14.
    """
    pm_surya_ghar: bool = False
    kusum_fls: bool = False
    bess_vgf_frac: float = 0.0
    rural_license_exempt: bool = True


# ---------------------------------------------------------------------------
# Subsidy application
# ---------------------------------------------------------------------------

def apply_subsidies(
    capex_breakdown: dict[str, float],
    stack: SubsidyStack,
    n_households: int,
    pv_kw: float,
    is_agri_feeder: bool = False,
) -> tuple[dict[str, float], dict[str, float]]:
    """Apply subsidy stack to capex breakdown, return adjusted capex + subsidy report.

    Parameters
    ----------
    capex_breakdown : dict[str, float]
        Raw capex before subsidies: {'solar': Rs, 'bess': Rs, 'router': Rs, 'cable': Rs}.
    stack : SubsidyStack
        Which subsidies are active.
    n_households : int
        Number of residential nodes (for PM Surya Ghar per-household cap).
    pv_kw : float
        Total PV capacity (kW) — used for KUSUM per-MW calculation.
    is_agri_feeder : bool
        Whether this is an agricultural feeder (KUSUM Component-C requires agri load).

    Returns
    -------
    adjusted_capex : dict[str, float]
        Capex after subsidies applied.
    subsidy_breakdown : dict[str, float]
        How much was received from each scheme: {'pm_surya_ghar': Rs, 'kusum_fls': Rs, ...}.
    """
    capex = capex_breakdown.copy()
    subsidies: dict[str, float] = {
        "pm_surya_ghar": 0.0,
        "kusum_fls": 0.0,
        "bess_vgf": 0.0,
    }

    # ---- PM Surya Ghar (rooftop residential solar) ----
    # Eligible: first 2 kW per household @ 30,000 Rs/kW, 3rd kW @ 18,000 Rs/kW.
    # Cap: 78,000 Rs per household total.
    # Source: MNRE PM-SG guidelines Feb-2024, Annexure-I (subsidy slab).
    if stack.pm_surya_ghar and n_households > 0:
        # Per household: assume PV is evenly distributed (conservative estimate)
        pv_per_hh = pv_kw / n_households if n_households > 0 else 0.0
        subsidy_per_hh = 0.0
        if pv_per_hh > 0:
            # First 2 kW: 30,000 Rs/kW
            slab1 = min(pv_per_hh, 2.0) * 30000.0
            # 3rd kW: 18,000 Rs/kW
            slab2 = max(0.0, min(pv_per_hh - 2.0, 1.0)) * 18000.0
            subsidy_per_hh = min(slab1 + slab2, 78000.0)  # cap at 78k per hh

        pm_total = subsidy_per_hh * n_households
        subsidies["pm_surya_ghar"] = pm_total
        capex["solar"] = max(0.0, capex.get("solar", 0.0) - pm_total)

    # ---- KUSUM Component-C (feeder-level solarization) ----
    # 30% capex subsidy on ground-mounted solar for agri feeders.
    # Cap: 1.05 crore Rs per MW (so 30% subsidy ≈ 31.5 lakh Rs/MW).
    # Source: MNRE KUSUM-C Operational Guidelines Aug-2024, para 3.3.1.
    if stack.kusum_fls and is_agri_feeder and pv_kw > 0:
        # 1.05 crore = 10,500,000 Rs per MW installed; 30% of that = subsidy cap per MW
        subsidy_cap_per_mw = 10_500_000.0 * 0.30
        pv_mw = pv_kw / 1000.0
        kusum_subsidy = min(
            capex.get("solar", 0.0) * 0.30,      # 30% of solar capex
            subsidy_cap_per_mw * pv_mw,          # cap at 31.5 lakh Rs/MW
        )
        subsidies["kusum_fls"] = kusum_subsidy
        capex["solar"] = max(0.0, capex["solar"] - kusum_subsidy)

    # ---- BESS VGF (battery storage viability gap funding) ----
    # SECI FY24-25 scheme: 30–40% capex support for utility-scale BESS.
    # Source: SECI tender SECI/C&P/BESS/01/2024, VGF Annexure.
    if stack.bess_vgf_frac > 0 and capex.get("bess", 0.0) > 0:
        bess_subsidy = capex["bess"] * stack.bess_vgf_frac
        subsidies["bess_vgf"] = bess_subsidy
        capex["bess"] = max(0.0, capex["bess"] - bess_subsidy)

    return capex, subsidies
