"""
economics.py — Financial analysis layer for DC microgrid simulations.

Computes:
  - CAPEX (solar, BESS, routers, cable) with subsidy application
  - Annual cost (CRF + O&M)
  - Revenue streams (energy sales, P2P, diesel displacement, demand charge savings, carbon)
  - LCOE / LCOS / payback / NPV / viable tariff

All cost parameters are India 2026 commercial EPC rates with sources cited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .simulate import SimResults
from .subsidies import SubsidyStack, apply_subsidies
from .tariffs import TariffSchedule, energy_charge, monthly_fixed_and_demand


# ---------------------------------------------------------------------------
# Cost dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CapexConfig:
    """Capital cost assumptions (India 2026 commercial EPC rates).

    All values in Rs (Indian rupees) unless otherwise noted.

    Sources:
      solar_rs_per_kw:
        MNRE Benchmark Cost for Grid-Connected Rooftop Solar FY 2025-26.
        Notification MNRE/11/18/2023-GCRT dated 01-Apr-2025.
        1–10 kW: 48,000 Rs/kW; 10–100 kW: 43,000 Rs/kW; 100+ kW: 40,000 Rs/kW.
        We use 45,000 Rs/kW (blended avg for 20–50 kW community systems).

      bess_rs_per_kwh:
        Ember / IEEFA India Battery Storage Cost Tracker Q1-2026.
        All-in EPC (battery cells + BMS + inverter + install + 10-yr warranty):
        $115–125/kWh delivered = ₹9,500–10,500/kWh at ₹83/USD (Apr-2026).
        We use 10,000 Rs/kWh (conservative mid-point).
        Ref: IEEFA report "India Energy Storage Outlook 2026", Fig. 3.2.

      router_rs_per_node:
        Phase 1 hardware BOM estimate (custom PCB + magnetics + casing + install).
        4-port router (solar + battery + 2×peer): ₹1,20,000–1,80,000 per node
        depending on BOM scatter and labor. We use 1,50,000 Rs/node (mid-estimate).
        This is speculative; not yet manufactured at scale.

      dc_cable_rs_per_km:
        25 mm² copper DC cable (2-core, UV-rated, -40 to +90°C).
        Material: ₹4,50,000/km; trenching + conduit + labor: ₹3,50,000/km.
        Total: ₹8,00,000/km installed (rural UP / peri-urban tariff).
        Source: CPWD DSR 2024 Vol-II (Electrical Works), Item 16.22 (LT Cable).

      om_frac_per_year:
        Annual O&M as fraction of capex: 1% is standard for solar+BESS systems.
        Source: NREL 2024 ATB (Solar PV / Battery), O&M Fixed Cost $15/kW-yr
        ≈ 1% of $1500/kW capex. Indian practice: similar or slightly lower.
    """
    solar_rs_per_kw: float = 45000.0        # MNRE benchmark FY25-26, rooftop 10-100 kW
    bess_rs_per_kwh: float = 10000.0        # Ember/IEEFA Q1-2026, $115-125/kWh all-in
    router_rs_per_node: float = 150000.0    # Phase 1 BOM estimate (4-port custom router)
    dc_cable_rs_per_km: float = 800000.0    # 25 mm² Cu + trenching, CPWD DSR 2024
    om_frac_per_year: float = 0.01          # 1% capex/year (NREL ATB 2024)


@dataclass
class FinanceConfig:
    """Financial parameters for NPV / LCOE / payback calculations.

    discount_rate:
        Weighted average cost of capital (WACC) for rural/peri-urban microgrid.
        RBI 2025: MCLR (1-yr) ≈ 8.5%; add 150–200 bps margin for project finance
        → 10–11%. We use 10% (conservative). For pure-equity model, treat as
        opportunity cost of capital (bank FD ≈ 7%, equity hurdle ≈ 12–15%).
        Source: SBI MCLR rates Mar-2026; CEEW microfinance model (2024).

    project_life_years:
        Standard PV panel warranty: 25 years linear (80% output @ yr 25).
        Battery: 10-yr warranty typical, but system economic life = 25 yrs with
        one mid-life battery replacement (accounted separately if modeled).
        Source: IEC 61215 (PV), IEC 62619 (Li-ion ESS).
    """
    discount_rate: float = 0.10      # 10% WACC (MCLR + margin, rural India)
    project_life_years: int = 25     # Standard PV + BESS project life


# ---------------------------------------------------------------------------
# Financial functions
# ---------------------------------------------------------------------------

def crf(rate: float, years: int) -> float:
    """Capital recovery factor: annualize a present-value capex over N years.

    CRF = r(1+r)^N / [(1+r)^N - 1]

    Parameters
    ----------
    rate : float
        Annual discount rate (fraction, e.g. 0.10 for 10%).
    years : int
        Project life in years.

    Returns
    -------
    float
        Annualization factor (dimensionless).
    """
    if rate < 1e-9:
        return 1.0 / years if years > 0 else 0.0
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def lcoe(
    capex: float,
    om_annual: float,
    annual_kwh: float,
    fin: FinanceConfig,
) -> float:
    """Levelized cost of energy (Rs/kWh).

    LCOE = (CAPEX × CRF + O&M_annual) / annual_kWh

    Parameters
    ----------
    capex : float
        Total upfront capex (Rs).
    om_annual : float
        Annual O&M cost (Rs/year).
    annual_kwh : float
        Annual energy delivered (kWh/year).
    fin : FinanceConfig

    Returns
    -------
    float
        LCOE in Rs/kWh.
    """
    if annual_kwh < 1e-6:
        return float("inf")
    annual_cost = capex * crf(fin.discount_rate, fin.project_life_years) + om_annual
    return annual_cost / annual_kwh


def lcos_battery(
    capex: float,
    annual_kwh_throughput: float,
    fin: FinanceConfig,
    rte: float = 0.95,
) -> float:
    """Levelized cost of storage (Rs/kWh of throughput).

    LCOS = (CAPEX × CRF) / (annual_throughput × RTE)

    Parameters
    ----------
    capex : float
        Battery system capex (Rs).
    annual_kwh_throughput : float
        Total annual charge+discharge throughput (kWh/year).
    fin : FinanceConfig
    rte : float
        Round-trip efficiency (0–1).

    Returns
    -------
    float
        LCOS in Rs/kWh delivered.
    """
    if annual_kwh_throughput < 1e-6:
        return float("inf")
    annual_cost = capex * crf(fin.discount_rate, fin.project_life_years)
    return annual_cost / (annual_kwh_throughput * rte)


# ---------------------------------------------------------------------------
# Economics result container
# ---------------------------------------------------------------------------

@dataclass
class EconResult:
    """Full financial analysis output for one simulation scenario.

    All monetary values in Rs (Indian rupees).
    All annualized values are steady-state annual (not just the simulation period).
    """
    # Capex
    capex_total_rs: float = 0.0
    capex_after_subsidies_rs: float = 0.0
    subsidy_breakdown: dict[str, float] = field(default_factory=dict)

    # Annual costs
    annual_cost_rs: float = 0.0                  # CRF × capex + O&M

    # Annual revenue (absolute — what the mesh earns)
    revenue_energy_sales_rs: float = 0.0         # load served × blended tariff
    revenue_p2p_rs: float = 0.0                  # grid export × P2P price − txn charges
    revenue_carbon_rs: float = 0.0               # solar_kwh × grid_ef × carbon_price

    # Annual expenses (absolute — what the mesh spends)
    cost_grid_import_rs: float = 0.0             # grid import × blended tariff
    cost_fuel_rs: float = 0.0                    # diesel consumed by mesh gensets
    cost_voll_penalty_rs: float = 0.0            # mesh shed_kwh × VOLL

    # Summary metrics
    net_annual_rs: float = 0.0                   # total revenue - total cost
    payback_years: float = float("inf")
    npv_rs: float = 0.0
    lcoe_mesh_rs_per_kwh: float = float("inf")   # annual_cost / total_load_served
    viable_tariff_rs_per_kwh: float = float("inf")  # tariff at which net_annual = 0
    discount_rate: float = 0.10                  # stored for reporting

    def summary_table(self) -> str:
        """Formatted text summary (absolute P&L frame — no delta/displacement lines)."""
        lines = [
            "=== Economics Summary (absolute frame) ===",
            f"  CAPEX (raw)               : ₹{self.capex_total_rs:,.0f}",
            f"  CAPEX (after subsidies)   : ₹{self.capex_after_subsidies_rs:,.0f}",
        ]
        for scheme, amt in self.subsidy_breakdown.items():
            if amt > 0:
                lines.append(f"    - {scheme:<20} : ₹{amt:,.0f}")
        lines.extend([
            f"  Annual cost (CRF + O&M)   : ₹{self.annual_cost_rs:,.0f}",
            "",
            "  Revenue (annual):",
            f"    Energy sales            : ₹{self.revenue_energy_sales_rs:,.0f}",
            f"    P2P export              : ₹{self.revenue_p2p_rs:,.0f}",
            f"    Carbon credits          : ₹{self.revenue_carbon_rs:,.0f}",
            "",
            "  Costs (annual):",
            f"    Capex + O&M (annualized): ₹{self.annual_cost_rs:,.0f}",
            f"    Grid import             : ₹{self.cost_grid_import_rs:,.0f}",
            f"    Fuel (diesel/genset)    : ₹{self.cost_fuel_rs:,.0f}",
            f"    VOLL penalty (shed)     : ₹{self.cost_voll_penalty_rs:,.0f}",
            "",
            f"  Net annual cashflow       : ₹{self.net_annual_rs:,.0f}",
            f"  Payback period            : {self.payback_years:.1f} years",
            f"  NPV @ {self.discount_rate*100:.0f}% discount     : ₹{self.npv_rs:,.0f}",
            f"  LCOE (mesh)               : ₹{self.lcoe_mesh_rs_per_kwh:.2f} / kWh",
            f"  Viable tariff (breakeven) : ₹{self.viable_tariff_rs_per_kwh:.2f} / kWh",
        ])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main economics computation
# ---------------------------------------------------------------------------

def compute_economics(
    results: SimResults,
    market: "MarketConfig",  # type: ignore  # forward reference to markets.py
    baseline_results: SimResults | None = None,
) -> EconResult:
    """Compute full financial analysis from simulation results.

    Parameters
    ----------
    results : SimResults
        Mesh simulation output (with PV, battery, community dispatch).
    market : MarketConfig
        Market scenario config (tariffs, subsidies, capex, finance, prices).
    baseline_results : SimResults | None
        Counterfactual "no mesh" baseline (same loads, no PV/battery, grid-only).
        If provided, diesel displacement and VOLL are computed as deltas.
        If None, assume baseline = grid-only (all load imported, no shed).

    Returns
    -------
    EconResult
        Full financial breakdown.
    """
    econ = EconResult()
    econ.discount_rate = market.finance.discount_rate
    df = results.to_dataframe()
    dt_h = results.dt_h
    node_cfg = results.node_cfg

    # Annualization factor: simulation period → full year
    # Assume simulation is representative of a full year (if 7 days, scale × 52.14)
    sim_hours = len(df) * dt_h
    sim_days = sim_hours / 24.0
    annualize = 365.25 / sim_days

    # ---- CAPEX ----
    total_pv_kw = sum(cfg.get("pv_kw", 0.0) for cfg in node_cfg.values())
    total_bess_kwh = sum(cfg.get("battery_kwh", 0.0) for cfg in node_cfg.values())
    n_nodes = len(node_cfg)

    # Cable length: sum from topology (approx from node count for now)
    # Real topology is in network.py; here we estimate 60 m avg per node.
    cable_km_estimate = n_nodes * 0.060

    capex_raw = {
        "solar": total_pv_kw * market.capex.solar_rs_per_kw,
        "bess": total_bess_kwh * market.capex.bess_rs_per_kwh,
        "router": n_nodes * market.capex.router_rs_per_node,
        "cable": cable_km_estimate * market.capex.dc_cable_rs_per_km,
    }
    econ.capex_total_rs = sum(capex_raw.values())

    # Apply subsidies
    n_households = sum(
        1 for nid, cfg in node_cfg.items()
        if cfg.get("node_type", "residential") in ("residential", "apartment")
    )
    is_agri = any(
        cfg.get("node_type") == "pump" for cfg in node_cfg.values()
    )
    capex_after, subsidies = apply_subsidies(
        capex_raw, market.subsidies, n_households, total_pv_kw, is_agri
    )
    econ.capex_after_subsidies_rs = sum(capex_after.values())
    econ.subsidy_breakdown = subsidies

    # O&M
    om_annual = econ.capex_after_subsidies_rs * market.capex.om_frac_per_year

    # Annual cost = CRF × capex + O&M
    econ.annual_cost_rs = (
        econ.capex_after_subsidies_rs * crf(market.finance.discount_rate, market.finance.project_life_years)
        + om_annual
    )

    # ---- Load served ----
    load_cols = [c for c in df.columns if c.endswith("_load_kw")]
    total_load_kwh_sim = float(df[load_cols].sum().sum() * dt_h) if load_cols else 0.0
    total_load_kwh_annual = total_load_kwh_sim * annualize

    # ---- Grid import / export ----
    grid_kw = df["grid_exchange_kw"]
    grid_import_kw = grid_kw.clip(lower=0)
    grid_export_kw = (-grid_kw).clip(lower=0)
    grid_import_kwh_sim = float(grid_import_kw.sum() * dt_h)
    grid_export_kwh_sim = float(grid_export_kw.sum() * dt_h)
    grid_import_kwh_annual = grid_import_kwh_sim * annualize
    grid_export_kwh_annual = grid_export_kwh_sim * annualize

    # ---- Solar generation ----
    solar_cols = [c for c in df.columns if c.endswith("_solar_backplane_kw")]
    solar_kwh_sim = float(df[solar_cols].sum().sum() * dt_h) if solar_cols else 0.0
    solar_kwh_annual = solar_kwh_sim * annualize

    # ---- Shed load ----
    shed_kwh_sim = float(df["shed_load_kw"].sum() * dt_h) if "shed_load_kw" in df.columns else 0.0
    shed_kwh_annual = shed_kwh_sim * annualize

    # ---- Generator fuel (mesh absolute) ----
    gen_fuel_cols = [c for c in df.columns if c.endswith("_generator_fuel_L")]
    fuel_L_sim = float(df[gen_fuel_cols].sum().sum()) if gen_fuel_cols else 0.0
    fuel_L_annual = fuel_L_sim * annualize
    # Diesel: SFC ≈ 0.30 L/kWh → cost = fuel_L × (price_per_L / SFC_L_per_kWh)
    # Simpler: fuel_L × price_per_L. Diesel ₹90.6/L (Apr-2026 avg).
    diesel_price_per_L = 90.6  # Rs/L, Petrol Diesel Price tracker Apr-2026
    econ.cost_fuel_rs = fuel_L_annual * diesel_price_per_L

    # ---- VOLL penalty (mesh shed — absolute, not delta) ----
    econ.cost_voll_penalty_rs = shed_kwh_annual * market.voll_rs_per_kwh

    # ---- Revenue: energy sales ----
    # Blended tariff across node types using actual tariff slabs.
    # Estimate monthly consumption per node, apply slab billing, annualize.
    from .tariffs import TARIFFS, NODE_TARIFF_MAP, energy_charge as _energy_charge
    total_energy_sales_sim = 0.0
    total_grid_import_cost_sim = 0.0
    for nid, cfg in node_cfg.items():
        ntype = cfg.get("node_type", "residential")
        tariff_key = market.tariff_map.get(nid, NODE_TARIFF_MAP.get(ntype, "rural_domestic_up"))
        schedule = TARIFFS.get(tariff_key)
        if schedule is None:
            continue
        # Node load served this sim period
        load_col = f"{nid}_load_kw"
        if load_col not in df.columns:
            continue
        node_kwh_sim = float(df[load_col].sum() * dt_h)
        # Monthly equivalent for slab billing (sim period → 30 days)
        node_kwh_monthly = node_kwh_sim / sim_days * 30.0
        total_energy_sales_sim += _energy_charge(schedule, node_kwh_monthly) * (sim_days / 30.0)
    econ.revenue_energy_sales_rs = total_energy_sales_sim * annualize

    # Blended rate for grid import cost (use load-weighted avg)
    if total_load_kwh_annual > 0 and total_energy_sales_sim > 0:
        blended_rate = (total_energy_sales_sim * annualize) / total_load_kwh_annual
    else:
        blended_rate = 6.0
    econ.cost_grid_import_rs = grid_import_kwh_annual * blended_rate

    # ---- Revenue: P2P export ----
    econ.revenue_p2p_rs = grid_export_kwh_annual * market.p2p_price_rs_per_kwh
    econ.revenue_p2p_rs -= grid_export_kwh_annual * market.p2p_transaction_charge_rs_per_kwh

    # ---- Revenue: carbon credits ----
    # India grid emission factor: 0.71 kg CO₂/kWh (CEA CO₂ Baseline Database v19, 2024)
    carbon_avoided_tonnes = solar_kwh_annual * 0.71 / 1000.0
    econ.revenue_carbon_rs = carbon_avoided_tonnes * market.carbon_price_rs_per_tonne

    # ---- Summary metrics (absolute P&L, no deltas) ----
    total_revenue = (
        econ.revenue_energy_sales_rs
        + econ.revenue_p2p_rs
        + econ.revenue_carbon_rs
    )
    total_costs = (
        econ.annual_cost_rs
        + econ.cost_grid_import_rs
        + econ.cost_fuel_rs
        + econ.cost_voll_penalty_rs
    )
    econ.net_annual_rs = total_revenue - total_costs

    # Payback: capex / net annual (only if positive)
    if econ.net_annual_rs > 0:
        econ.payback_years = econ.capex_after_subsidies_rs / econ.net_annual_rs
    else:
        econ.payback_years = float("inf")

    # NPV
    npv_sum = 0.0
    for yr in range(1, market.finance.project_life_years + 1):
        npv_sum += econ.net_annual_rs / ((1 + market.finance.discount_rate) ** yr)
    econ.npv_rs = npv_sum - econ.capex_after_subsidies_rs

    # LCOE (all-in cost per kWh served)
    if total_load_kwh_annual > 0:
        econ.lcoe_mesh_rs_per_kwh = total_costs / total_load_kwh_annual
    else:
        econ.lcoe_mesh_rs_per_kwh = float("inf")

    # Viable tariff: the per-kWh rate at which revenue = costs
    # energy_sales ∝ tariff × load_kwh, so solve: tariff × load = costs - P2P - carbon
    non_tariff_revenue = econ.revenue_p2p_rs + econ.revenue_carbon_rs
    if total_load_kwh_annual > 0:
        econ.viable_tariff_rs_per_kwh = (total_costs - non_tariff_revenue) / total_load_kwh_annual
    else:
        econ.viable_tariff_rs_per_kwh = float("inf")

    return econ
