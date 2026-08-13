"""
econ_scenarios.py — Economic scenario runners E1/E2/E3 (rural / peri-urban / urban).

Each runner:
  1. Builds load/PV profiles from market node_cfg
  2. Runs BASELINE counterfactual (no PV/battery, grid-only + outages)
  3. Runs MESH case (full system with CommunityGreedyPolicy)
  4. Computes metrics + economics for both
  5. Runs LEVER SWEEP (subsidies off/on, P2P price, battery scale)
  6. Saves CSVs to data/results/econ/
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .dispatch import (
    CommunityGreedyPolicy,
    GeneratorAwareCommunityPolicy,
    OutageOnlyGensetPolicy,
    OutagePreChargePolicy,
)
from .economics import compute_economics, EconResult
from .markets import MARKETS, MarketConfig
from .metrics import compute_metrics, Metrics
from .profiles import load_profiles, pv_profiles
from .scenarios import _ensure_dir
from .simulate import run_simulation, SimResults


# ---------------------------------------------------------------------------
# Shared profile builder (same as scenarios._make_profiles, reused)
# ---------------------------------------------------------------------------

def _make_profiles_for_market(
    market: MarketConfig,
    dt_min: int = 15,
    horizon_h: float = 168.0,  # 7 days default
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate load and PV profiles for a market scenario.

    Uses the market's node_cfg directly, which includes node_type for each node.
    Weather: mixed_week (3 sunny + 1 cloudy + 3 sunny).
    """
    n_days = max(1, int(np.ceil(horizon_h / 24)))
    from .profiles import generate_weather_sequence

    daily_scales = generate_weather_sequence(n_days=n_days, season="mixed_week", seed=seed)

    load_df = load_profiles(
        market="india_semi_urban",
        node_cfg=market.node_cfg,
        dt_min=dt_min,
        horizon_h=horizon_h,
        seed=seed,
        start="2024-06-21",
    )
    pv_df = pv_profiles(
        node_cfg=market.node_cfg,
        dt_min=dt_min,
        horizon_h=horizon_h,
        irradiance_scale=0.85,   # mixed week avg
        daily_irradiance_scales=daily_scales,
        seed=seed,
        start="2024-06-21",
    )
    return load_df, pv_df


def _build_outage_steps(
    outage_windows: list[tuple[float, float]],
    horizon_h: float,
    dt_h: float,
) -> set[int]:
    """Convert daily outage windows [(start_h, end_h), ...] to step indices.

    Outages recur at the same local time each day.
    """
    steps_per_day = round(24.0 / dt_h)
    n_steps = int(horizon_h / dt_h)
    outage_steps: set[int] = set()

    for i in range(n_steps):
        local_h = (i % steps_per_day) * dt_h
        for start_h, end_h in outage_windows:
            if start_h <= local_h < end_h:
                outage_steps.add(i)
                break

    return outage_steps


# ---------------------------------------------------------------------------
# E1: Rural
# ---------------------------------------------------------------------------

def run_e1_rural(
    out_dir: str | Path = "data/results/econ",
    dt_min: int = 15,
    horizon_h: float = 168.0,  # 7 days
    save: bool = True,
) -> dict[str, Any]:
    """E1: Rural western UP village scenario.

    Baseline: no PV/battery, grid + diesel genset during outages.
    Mesh: full system with CommunityGreedyPolicy.
    Lever sweep: subsidies off/on, P2P price [0, 3, 4.5, 6], battery scale [0.5x, 1x, 1.5x].
    """
    market = MARKETS["rural"]
    print(f"\n{'='*80}")
    print(f"E1: RURAL (Western UP village)")
    print(f"{'='*80}")
    print(f"  Nodes: {list(market.node_cfg.keys())}")
    print(f"  Outages: {market.baseline_outage_hours_per_day:.1f} h/day, windows {market.outage_windows}")
    print(f"  Subsidies: PM Surya Ghar={market.subsidies.pm_surya_ghar}, KUSUM={market.subsidies.kusum_fls}, BESS VGF={market.subsidies.bess_vgf_frac:.0%}")
    print()

    dt_h = dt_min / 60.0
    load_df, pv_df = _make_profiles_for_market(market, dt_min, horizon_h)
    outage_steps = _build_outage_steps(market.outage_windows, horizon_h, dt_h)

    # ---- BASELINE (no PV/battery, grid + diesel genset during outages) ----
    baseline_cfg = {}
    for nid, cfg in market.node_cfg.items():
        base_node = {**cfg, "pv_kw": 0.0, "battery_kwh": 0.0}
        # CRITICAL: Disable deferral in baseline to ensure identical load profiles
        # Baseline = "grid-only" reality where pumps run on schedule (no solar to shift to)
        base_node["is_deferrable"] = False
        # Add diesel genset to non-grid nodes for outage backup
        if not cfg.get("has_grid_port", False) and cfg.get("avg_load_kw", 0.0) > 0:
            # Size genset to peak load (approx 2x avg load for residential)
            peak_load_kw = cfg.get("avg_load_kw", 0.0) * 2.5
            base_node["generator_kw"] = peak_load_kw
            base_node["gen_fuel_type"] = "diesel"
            base_node["gen_min_load_frac"] = 0.25
        baseline_cfg[nid] = base_node
    
    baseline_results = run_simulation(
        load_df, pv_df,
        policy=OutageOnlyGensetPolicy(outage_windows=market.outage_windows),
        node_cfg=baseline_cfg,
        policy_name="baseline_grid_diesel",
        grid_outage_steps=outage_steps,
    )
    baseline_metrics = compute_metrics(baseline_results)

    # ---- MESH (solar + battery + outage-aware pre-charge + genset backup) ----
    mesh_results = run_simulation(
        load_df, pv_df,
        policy=OutagePreChargePolicy(
            outage_windows=market.outage_windows,
            pre_charge_minutes=30.0,
            gen_threshold_kw=0.2,
        ),
        node_cfg=market.node_cfg,
        policy_name="mesh_precharge",
        grid_outage_steps=outage_steps,
    )
    mesh_metrics = compute_metrics(mesh_results)

    # ---- Economics ----
    econ = compute_economics(mesh_results, market, baseline_results)

    # ---- Print summary ----
    print("\n  BASELINE (grid + outage-only genset, no PV/battery):")
    print(f"    Total load       : {baseline_metrics.total_load_kwh:.1f} kWh")
    print(f"    Grid import      : {baseline_metrics.grid_import_kwh:.1f} kWh")
    print(f"    Shed load        : {baseline_metrics.shed_load_kwh:.1f} kWh")
    print(f"    Generator fuel   : {baseline_metrics.generator_fuel_L:.1f} L")

    print("\n  MESH (PV + battery + pre-charge + genset backup):")
    print(f"    Total load       : {mesh_metrics.total_load_kwh:.1f} kWh")
    print(f"    Grid import      : {mesh_metrics.grid_import_kwh:.1f} kWh")
    print(f"    Shed load        : {mesh_metrics.shed_load_kwh:.1f} kWh")
    print(f"    Generator fuel   : {mesh_metrics.generator_fuel_L:.1f} L")
    print(f"    Self-sufficiency : {mesh_metrics.self_sufficiency_pct:.1f}%")
    # Diesel displacement & VOLL comparison
    delta_fuel = baseline_metrics.generator_fuel_L - mesh_metrics.generator_fuel_L
    delta_shed = baseline_metrics.shed_load_kwh - mesh_metrics.shed_load_kwh
    print(f"    Δ Fuel (base-mesh): {delta_fuel:+.1f} L ({'saved' if delta_fuel > 0 else 'worse'})")
    print(f"    Δ Shed (base-mesh): {delta_shed:+.1f} kWh ({'avoided' if delta_shed > 0 else 'worse'})")

    print(f"\n{econ.summary_table()}")

    # ---- Sanity check ----
    viable = econ.viable_tariff_rs_per_kwh
    subsidies_on = market.subsidies.pm_surya_ghar or market.subsidies.kusum_fls or market.subsidies.bess_vgf_frac > 0
    if subsidies_on:
        expected_range = (5.0, 8.0)
    else:
        expected_range = (8.0, 12.0)

    if expected_range[0] <= viable <= expected_range[1]:
        print(f"\n  ✓ SANITY CHECK PASS: Viable tariff {viable:.2f} Rs/kWh in expected range {expected_range}")
    else:
        print(f"\n  ⚠ SANITY CHECK WARNING: Viable tariff {viable:.2f} Rs/kWh outside expected range {expected_range}")
        print("  Dominant cost drivers:")
        print(f"    Annual cost (CRF + O&M) : ₹{econ.annual_cost_rs:,.0f}")
        print(f"    Grid import cost        : ₹{econ.cost_grid_import_rs:,.0f}")
        print(f"    Fuel cost               : ₹{econ.cost_fuel_rs:,.0f}")
        print(f"    VOLL penalty            : ₹{econ.cost_voll_penalty_rs:,.0f}")
        print(f"    CAPEX (after subsidies) : ₹{econ.capex_after_subsidies_rs:,.0f}")

    # ---- Lever sweep ----
    print("\n  LEVER SWEEP:")
    sweep_results = _run_lever_sweep(market, load_df, pv_df, baseline_results, outage_steps, dt_min)
    print("\n  Lever sweep table:")
    print(f"  {'Subsidies':<12} {'P2P price':<10} {'Batt scale':<12} {'Viable tariff':>15} {'Payback':>10} {'NPV':>15}")
    print(f"  {'-'*80}")
    for key, econ_result in sweep_results.items():
        print(f"  {key:<37} {econ_result.viable_tariff_rs_per_kwh:>14.2f} Rs/kWh {econ_result.payback_years:>9.1f} yr {econ_result.npv_rs:>14,.0f} Rs")

    # ---- Save CSVs ----
    if save:
        out_dir = _ensure_dir(out_dir)
        baseline_results.to_dataframe().to_csv(out_dir / "e1_rural_baseline.csv")
        mesh_results.to_dataframe().to_csv(out_dir / "e1_rural_mesh.csv")
        with open(out_dir / "e1_rural_econ.txt", "w") as f:
            f.write(econ.summary_table())
        # Sweep CSV
        sweep_rows = []
        for key, er in sweep_results.items():
            row = {
                "lever": key,
                "viable_tariff_rs_per_kwh": er.viable_tariff_rs_per_kwh,
                "payback_years": er.payback_years,
                "npv_rs": er.npv_rs,
                "capex_after_subsidies_rs": er.capex_after_subsidies_rs,
            }
            sweep_rows.append(row)
        pd.DataFrame(sweep_rows).to_csv(out_dir / "e1_rural_sweep.csv", index=False)
        print(f"\n  Saved: {out_dir}/e1_rural_*.csv")

    return {
        "baseline_results": baseline_results,
        "baseline_metrics": baseline_metrics,
        "mesh_results": mesh_results,
        "mesh_metrics": mesh_metrics,
        "econ": econ,
        "sweep": sweep_results,
    }


# ---------------------------------------------------------------------------
# E2: Peri-urban
# ---------------------------------------------------------------------------

def run_e2_peri_urban(
    out_dir: str | Path = "data/results/econ",
    dt_min: int = 15,
    horizon_h: float = 168.0,
    save: bool = True,
) -> dict[str, Any]:
    """E2: Peri-urban Meerut-type scenario (residential + shop + cold storage)."""
    market = MARKETS["peri_urban"]
    print(f"\n{'='*80}")
    print(f"E2: PERI-URBAN (Meerut-type: res + shop + cold storage)")
    print(f"{'='*80}")
    print(f"  Nodes: {list(market.node_cfg.keys())}")
    print(f"  Outages: {market.baseline_outage_hours_per_day:.1f} h/day, windows {market.outage_windows}")
    print(f"  Subsidies: PM Surya Ghar={market.subsidies.pm_surya_ghar}, BESS VGF={market.subsidies.bess_vgf_frac:.0%}")
    print()

    dt_h = dt_min / 60.0
    load_df, pv_df = _make_profiles_for_market(market, dt_min, horizon_h)
    outage_steps = _build_outage_steps(market.outage_windows, horizon_h, dt_h)

    # ---- BASELINE ----
    baseline_cfg = {}
    for nid, cfg in market.node_cfg.items():
        base_node = {**cfg, "pv_kw": 0.0, "battery_kwh": 0.0}
        base_node["is_deferrable"] = False  # Disable deferral for identical load profiles
        if not cfg.get("has_grid_port", False) and cfg.get("avg_load_kw", 0.0) > 0:
            peak_load_kw = cfg.get("avg_load_kw", 0.0) * 2.5
            base_node["generator_kw"] = peak_load_kw
            base_node["gen_fuel_type"] = "diesel"
            base_node["gen_min_load_frac"] = 0.25
        baseline_cfg[nid] = base_node
    
    baseline_results = run_simulation(
        load_df, pv_df,
        policy=OutageOnlyGensetPolicy(outage_windows=market.outage_windows),
        node_cfg=baseline_cfg,
        policy_name="baseline_grid_diesel",
        grid_outage_steps=outage_steps,
    )
    baseline_metrics = compute_metrics(baseline_results)

    # ---- MESH ----
    mesh_results = run_simulation(
        load_df, pv_df,
        policy=OutagePreChargePolicy(
            outage_windows=market.outage_windows,
            pre_charge_minutes=30.0,
            gen_threshold_kw=0.2,
        ),
        node_cfg=market.node_cfg,
        policy_name="mesh_precharge",
        grid_outage_steps=outage_steps,
    )
    mesh_metrics = compute_metrics(mesh_results)

    # ---- Economics ----
    econ = compute_economics(mesh_results, market, baseline_results)

    # ---- Print ----
    print("\n  BASELINE (grid + outage-only genset, no PV/battery):")
    print(f"    Total load       : {baseline_metrics.total_load_kwh:.1f} kWh")
    print(f"    Grid import      : {baseline_metrics.grid_import_kwh:.1f} kWh")
    print(f"    Shed load        : {baseline_metrics.shed_load_kwh:.1f} kWh")
    print(f"    Generator fuel   : {baseline_metrics.generator_fuel_L:.1f} L")

    print("\n  MESH (PV + battery + pre-charge + genset backup):")
    print(f"    Total load       : {mesh_metrics.total_load_kwh:.1f} kWh")
    print(f"    Grid import      : {mesh_metrics.grid_import_kwh:.1f} kWh")
    print(f"    Shed load        : {mesh_metrics.shed_load_kwh:.1f} kWh")
    print(f"    Generator fuel   : {mesh_metrics.generator_fuel_L:.1f} L")
    print(f"    Self-sufficiency : {mesh_metrics.self_sufficiency_pct:.1f}%")
    delta_fuel = baseline_metrics.generator_fuel_L - mesh_metrics.generator_fuel_L
    delta_shed = baseline_metrics.shed_load_kwh - mesh_metrics.shed_load_kwh
    print(f"    Δ Fuel (base-mesh): {delta_fuel:+.1f} L ({'saved' if delta_fuel > 0 else 'worse'})")
    print(f"    Δ Shed (base-mesh): {delta_shed:+.1f} kWh ({'avoided' if delta_shed > 0 else 'worse'})")

    print(f"\n{econ.summary_table()}")

    # ---- Lever sweep ----
    print("\n  LEVER SWEEP:")
    sweep_results = _run_lever_sweep(market, load_df, pv_df, baseline_results, outage_steps, dt_min)
    print(f"  {'Subsidies':<12} {'P2P price':<10} {'Batt scale':<12} {'Viable tariff':>15} {'Payback':>10} {'NPV':>15}")
    print(f"  {'-'*80}")
    for key, econ_result in sweep_results.items():
        print(f"  {key:<37} {econ_result.viable_tariff_rs_per_kwh:>14.2f} Rs/kWh {econ_result.payback_years:>9.1f} yr {econ_result.npv_rs:>14,.0f} Rs")

    # ---- Save ----
    if save:
        out_dir = _ensure_dir(out_dir)
        baseline_results.to_dataframe().to_csv(out_dir / "e2_peri_urban_baseline.csv")
        mesh_results.to_dataframe().to_csv(out_dir / "e2_peri_urban_mesh.csv")
        with open(out_dir / "e2_peri_urban_econ.txt", "w") as f:
            f.write(econ.summary_table())
        sweep_rows = []
        for key, er in sweep_results.items():
            sweep_rows.append({
                "lever": key,
                "viable_tariff_rs_per_kwh": er.viable_tariff_rs_per_kwh,
                "payback_years": er.payback_years,
                "npv_rs": er.npv_rs,
            })
        pd.DataFrame(sweep_rows).to_csv(out_dir / "e2_peri_urban_sweep.csv", index=False)
        print(f"\n  Saved: {out_dir}/e2_peri_urban_*.csv")

    return {
        "baseline_results": baseline_results,
        "baseline_metrics": baseline_metrics,
        "mesh_results": mesh_results,
        "mesh_metrics": mesh_metrics,
        "econ": econ,
        "sweep": sweep_results,
    }


# ---------------------------------------------------------------------------
# E3: Urban
# ---------------------------------------------------------------------------

def run_e3_urban(
    out_dir: str | Path = "data/results/econ",
    dt_min: int = 15,
    horizon_h: float = 168.0,
    save: bool = True,
) -> dict[str, Any]:
    """E3: Urban Delhi mixed pocket (apartment + shop + PHC)."""
    market = MARKETS["urban"]
    print(f"\n{'='*80}")
    print(f"E3: URBAN (Delhi mixed: apartment + shop + PHC)")
    print(f"{'='*80}")
    print(f"  Nodes: {list(market.node_cfg.keys())}")
    print(f"  Outages: {market.baseline_outage_hours_per_day:.1f} h/day, windows {market.outage_windows}")
    print(f"  Subsidies: NONE (urban group ineligible for PM Surya Ghar / BESS VGF FY25)")
    print()

    dt_h = dt_min / 60.0
    load_df, pv_df = _make_profiles_for_market(market, dt_min, horizon_h)
    outage_steps = _build_outage_steps(market.outage_windows, horizon_h, dt_h)

    # ---- BASELINE ----
    baseline_cfg = {}
    for nid, cfg in market.node_cfg.items():
        base_node = {**cfg, "pv_kw": 0.0, "battery_kwh": 0.0}
        base_node["is_deferrable"] = False  # Disable deferral for identical load profiles
        if not cfg.get("has_grid_port", False) and cfg.get("avg_load_kw", 0.0) > 0:
            peak_load_kw = cfg.get("avg_load_kw", 0.0) * 2.5
            base_node["generator_kw"] = peak_load_kw
            base_node["gen_fuel_type"] = "diesel"
            base_node["gen_min_load_frac"] = 0.25
        baseline_cfg[nid] = base_node
    
    baseline_results = run_simulation(
        load_df, pv_df,
        policy=OutageOnlyGensetPolicy(outage_windows=market.outage_windows),
        node_cfg=baseline_cfg,
        policy_name="baseline_grid_diesel",
        grid_outage_steps=outage_steps,
    )
    baseline_metrics = compute_metrics(baseline_results)

    # ---- MESH ----
    mesh_results = run_simulation(
        load_df, pv_df,
        policy=OutagePreChargePolicy(
            outage_windows=market.outage_windows,
            pre_charge_minutes=30.0,
            gen_threshold_kw=0.2,
        ),
        node_cfg=market.node_cfg,
        policy_name="mesh_precharge",
        grid_outage_steps=outage_steps,
    )
    mesh_metrics = compute_metrics(mesh_results)

    # ---- Economics ----
    econ = compute_economics(mesh_results, market, baseline_results)

    # ---- Print ----
    print("\n  BASELINE (grid + outage-only genset, no PV/battery):")
    print(f"    Total load       : {baseline_metrics.total_load_kwh:.1f} kWh")
    print(f"    Grid import      : {baseline_metrics.grid_import_kwh:.1f} kWh")
    print(f"    Shed load        : {baseline_metrics.shed_load_kwh:.1f} kWh")
    print(f"    Generator fuel   : {baseline_metrics.generator_fuel_L:.1f} L")

    print("\n  MESH (PV + battery + pre-charge + genset backup):")
    print(f"    Total load       : {mesh_metrics.total_load_kwh:.1f} kWh")
    print(f"    Grid import      : {mesh_metrics.grid_import_kwh:.1f} kWh")
    print(f"    Shed load        : {mesh_metrics.shed_load_kwh:.1f} kWh")
    print(f"    Generator fuel   : {mesh_metrics.generator_fuel_L:.1f} L")
    print(f"    Self-sufficiency : {mesh_metrics.self_sufficiency_pct:.1f}%")
    delta_fuel = baseline_metrics.generator_fuel_L - mesh_metrics.generator_fuel_L
    delta_shed = baseline_metrics.shed_load_kwh - mesh_metrics.shed_load_kwh
    print(f"    Δ Fuel (base-mesh): {delta_fuel:+.1f} L ({'saved' if delta_fuel > 0 else 'worse'})")
    print(f"    Δ Shed (base-mesh): {delta_shed:+.1f} kWh ({'avoided' if delta_shed > 0 else 'worse'})")

    print(f"\n{econ.summary_table()}")

    # ---- Lever sweep ----
    print("\n  LEVER SWEEP:")
    sweep_results = _run_lever_sweep(market, load_df, pv_df, baseline_results, outage_steps, dt_min)
    print(f"  {'Subsidies':<12} {'P2P price':<10} {'Batt scale':<12} {'Viable tariff':>15} {'Payback':>10} {'NPV':>15}")
    print(f"  {'-'*80}")
    for key, econ_result in sweep_results.items():
        print(f"  {key:<37} {econ_result.viable_tariff_rs_per_kwh:>14.2f} Rs/kWh {econ_result.payback_years:>9.1f} yr {econ_result.npv_rs:>14,.0f} Rs")

    # ---- Save ----
    if save:
        out_dir = _ensure_dir(out_dir)
        baseline_results.to_dataframe().to_csv(out_dir / "e3_urban_baseline.csv")
        mesh_results.to_dataframe().to_csv(out_dir / "e3_urban_mesh.csv")
        with open(out_dir / "e3_urban_econ.txt", "w") as f:
            f.write(econ.summary_table())
        sweep_rows = []
        for key, er in sweep_results.items():
            sweep_rows.append({
                "lever": key,
                "viable_tariff_rs_per_kwh": er.viable_tariff_rs_per_kwh,
                "payback_years": er.payback_years,
                "npv_rs": er.npv_rs,
            })
        pd.DataFrame(sweep_rows).to_csv(out_dir / "e3_urban_sweep.csv", index=False)
        print(f"\n  Saved: {out_dir}/e3_urban_*.csv")

    return {
        "baseline_results": baseline_results,
        "baseline_metrics": baseline_metrics,
        "mesh_results": mesh_results,
        "mesh_metrics": mesh_metrics,
        "econ": econ,
        "sweep": sweep_results,
    }


# ---------------------------------------------------------------------------
# Lever sweep helper
# ---------------------------------------------------------------------------

def _run_lever_sweep(
    market: MarketConfig,
    load_df: pd.DataFrame,
    pv_df: pd.DataFrame,
    baseline_results: SimResults,
    outage_steps: set[int],
    dt_min: int,
) -> dict[str, EconResult]:
    """Run lever sweep: subsidies off/on, P2P price, battery scale.

    Returns dict keyed by lever descriptor string → EconResult.
    """
    sweep: dict[str, EconResult] = {}

    # Levers
    subsidy_variants = [False, True]
    p2p_prices = [0.0, 3.0, 4.5, 6.0]
    battery_scales = [0.5, 1.0, 1.5]

    # Base case: subsidies on, default P2P price, 1x battery
    base_market = market
    base_cfg = market.node_cfg

    mesh_policy = OutagePreChargePolicy(
        outage_windows=base_market.outage_windows,
        pre_charge_minutes=30.0,
        gen_threshold_kw=0.2,
    )

    # Sweep: vary one lever at a time (keep others at baseline)
    for sub_on in subsidy_variants:
        m = copy.deepcopy(base_market)
        if not sub_on:
            m.subsidies.pm_surya_ghar = False
            m.subsidies.kusum_fls = False
            m.subsidies.bess_vgf_frac = 0.0
        mesh_r = run_simulation(load_df, pv_df, policy=mesh_policy,
                                 node_cfg=base_cfg, grid_outage_steps=outage_steps)
        econ = compute_economics(mesh_r, m, baseline_results)
        key = f"sub={'on' if sub_on else 'off'}_p2p={base_market.p2p_price_rs_per_kwh:.1f}_batt=1.0x"
        sweep[key] = econ

    for p2p in p2p_prices:
        m = copy.deepcopy(base_market)
        m.p2p_price_rs_per_kwh = p2p
        mesh_r = run_simulation(load_df, pv_df, policy=mesh_policy,
                                 node_cfg=base_cfg, grid_outage_steps=outage_steps)
        econ = compute_economics(mesh_r, m, baseline_results)
        key = f"sub=on_p2p={p2p:.1f}_batt=1.0x"
        sweep[key] = econ

    for scale in battery_scales:
        cfg = copy.deepcopy(base_cfg)
        for nid in cfg:
            if "battery_kwh" in cfg[nid]:
                cfg[nid]["battery_kwh"] = cfg[nid]["battery_kwh"] * scale
        sweep_policy = OutagePreChargePolicy(
            outage_windows=base_market.outage_windows,
            pre_charge_minutes=30.0,
            gen_threshold_kw=0.2,
        )
        mesh_r = run_simulation(load_df, pv_df, policy=sweep_policy,
                                 node_cfg=cfg, grid_outage_steps=outage_steps)
        econ = compute_economics(mesh_r, base_market, baseline_results)
        key = f"sub=on_p2p={base_market.p2p_price_rs_per_kwh:.1f}_batt={scale:.1f}x"
        sweep[key] = econ

    return sweep
