"""
scenarios.py — S0..S7 scenario runners.

Each scenario function returns a (SimResults, Metrics) tuple and optionally
saves CSVs and figures to an output directory.

Scenarios implemented here:
  S0 — Sunny baseline, 3 houses + farm, greedy policy
  S1 — 50% PV operational (meeting edge case)
  S2 — Cloudy / low-irradiance day
  S3 — Winter (low PV, high load)
  S4 — Scale 3 → 10 → 21 houses
  S5 — Battery sizing sweep
  S6 — Greedy vs predictive policy comparison
  S7 — Islanded (grid outage for N hours)
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .dispatch import (
    CommunityGreedyPolicy,
    CommunityPredictivePolicy,
    EVAwareCommunityPolicy,
    GreedyPolicy,
    PredictivePolicy,
    make_policy,
)
from .metrics import Metrics, compute_metrics
from .network import T1_STAR, T4_RING, T6_APARTMENT, T_DC_BUS, T_S_SPOKE, TopologyConfig
from .profiles import (
    SEASONAL_PARAMS,
    generate_weather_sequence,
    load_profiles,
    pv_profiles,
)
from .simulate import DEFAULT_NODE_CFG, SimResults, build_routers, run_simulation

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_results(
    results: SimResults,
    metrics: Metrics,
    out_dir: str | Path,
    label: str,
) -> None:
    """Save CSV + summary text for one scenario run."""
    out_dir = _ensure_dir(out_dir)
    df = results.to_dataframe()
    df.to_csv(out_dir / f"{label}_timeseries.csv")
    with open(out_dir / f"{label}_summary.txt", "w") as f:
        f.write(metrics.summary())
        f.write("\n")
    print(f"  Saved: {out_dir / label}_*.csv/txt")


# ---------------------------------------------------------------------------
# Shared profile loader
# ---------------------------------------------------------------------------

def _make_profiles(
    node_cfg: dict[str, dict],
    dt_min: int = 15,
    horizon_h: float = 24.0,
    irradiance_scale: float = 1.0,
    pv_fraction: float = 1.0,
    seed: int = 42,
    start: str = "2024-06-21",
    market: str = "india_semi_urban",
    weather_season: str | None = None,
    daily_irradiance_scales: list[float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate load and PV profiles.

    For multi-day runs pass ``horizon_h = 24 * n_days``.  If
    ``daily_irradiance_scales`` is supplied it overrides ``irradiance_scale``
    on a per-day basis (use ``generate_weather_sequence`` to build it).
    ``weather_season`` auto-generates a sequence from SEASONAL_PARAMS if
    neither override is given.
    """
    n_days = max(1, int(np.ceil(horizon_h / 24)))

    if daily_irradiance_scales is None and weather_season is not None:
        daily_irradiance_scales = generate_weather_sequence(
            n_days=n_days, season=weather_season, seed=seed
        )

    load_df = load_profiles(
        market=market,
        node_cfg=node_cfg,
        dt_min=dt_min,
        horizon_h=horizon_h,
        seed=seed,
        start=start,
    )
    pv_df = pv_profiles(
        node_cfg=node_cfg,
        dt_min=dt_min,
        horizon_h=horizon_h,
        irradiance_scale=irradiance_scale,
        daily_irradiance_scales=daily_irradiance_scales,
        pv_fraction=pv_fraction,
        seed=seed,
        start=start,
    )
    return load_df, pv_df


# ---------------------------------------------------------------------------
# Shared multi-policy runner
# ---------------------------------------------------------------------------

_DEFAULT_POLICIES: list[tuple[str, object]] = [
    ("greedy",    GreedyPolicy),
    ("community", CommunityGreedyPolicy),
]

def _run_policy_suite(
    label: str,
    load_df: "pd.DataFrame",
    pv_df: "pd.DataFrame",
    node_cfg: dict,
    approach: str,
    out_dir: "str | Path",
    save: bool,
    policy_factories: "list[tuple[str, type]]" = _DEFAULT_POLICIES,
) -> "dict[str, tuple[SimResults, Metrics]]":
    """Run a fixed set of profiles through each policy, print comparison, save results."""
    results: dict[str, tuple[SimResults, Metrics]] = {}
    header = False
    for name, cls in policy_factories:
        policy = cls()
        r = run_simulation(load_df, pv_df, policy=policy, node_cfg=node_cfg,
                           approach=approach, policy_name=name)
        m = compute_metrics(r)
        if not header:
            print(f"\n  {'Policy':<22} {'SS%':>6} {'SC%':>6} "
                  f"{'Grid import':>12} {'Curtail':>10} {'Losses':>8}")
            print("  " + "-" * 70)
            header = True
        print(
            f"  {name:<22} "
            f"{m.self_sufficiency_pct:>5.1f}% "
            f"{m.self_consumption_pct:>5.1f}% "
            f"{m.grid_import_kwh:>11.2f} kWh "
            f"{m.curtailed_kwh:>9.2f} kWh "
            f"{m.total_loss_kwh:>7.2f} kWh"
        )
        results[name] = (r, m)
        if save:
            save_results(r, m, out_dir, f"{label}_{name}")
    print()
    return results


# ---------------------------------------------------------------------------
# S0 — Sunny baseline
# ---------------------------------------------------------------------------

def run_s0(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """S0: sunny summer day(s), 3 houses + farm.

    Pipeline sanity check + baseline numbers for self-sufficiency,
    losses, and peer-sharing.  Runs greedy and community policies.
    Set horizon_days > 1 for multi-day runs (battery SoC carries over).
    """
    h = horizon_h * horizon_days
    label = f"S0_sunny_baseline{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running S0 — Sunny baseline ({horizon_days}d) ...")
    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG,
        dt_min=dt_min, horizon_h=h,
        irradiance_scale=1.0,
        weather_season="summer_sunny",
        start="2024-06-21",
    )
    return _run_policy_suite(label, load_df, pv_df, DEFAULT_NODE_CFG, approach, out_dir, save)


# ---------------------------------------------------------------------------
# S1 — 50% PV operational
# ---------------------------------------------------------------------------

def run_s1(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """S1: only 50% of PV panels operational (meeting edge case).

    Question: does the community still hold voltage, meet demand,
    and fall back gracefully to the grid?  Runs greedy and community.
    """
    h = horizon_h * horizon_days
    label = f"S1_50pct_pv{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running S1 — 50% PV ({horizon_days}d) ...")
    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG,
        dt_min=dt_min, horizon_h=h,
        pv_fraction=0.5, irradiance_scale=1.0,
        weather_season="summer_sunny",
        start="2024-06-21",
    )
    return _run_policy_suite(label, load_df, pv_df, DEFAULT_NODE_CFG, approach, out_dir, save)


# ---------------------------------------------------------------------------
# S2 — Cloudy day
# ---------------------------------------------------------------------------

def run_s2(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """S2: low-irradiance / overcast day(s) (irradiance_scale=0.25).

    Stress test: community nearly fully grid-dependent; batteries provide
    buffer but curtailment is near zero.
    """
    h = horizon_h * horizon_days
    label = f"S2_cloudy{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running S2 — Cloudy ({horizon_days}d) ...")
    params = SEASONAL_PARAMS["cloudy"]
    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=h,
        irradiance_scale=params["irradiance_scale"],
        weather_season=params["weather"],
        start=params["start"],
    )
    return _run_policy_suite(label, load_df, pv_df, DEFAULT_NODE_CFG, approach, out_dir, save)


# ---------------------------------------------------------------------------
# S3 — Winter
# ---------------------------------------------------------------------------

def run_s3(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """S3: winter day(s) — low PV (35%), high load (10% above average).

    Stress test: community nearly fully grid-dependent; community policy's
    night-buffer behaviour is critical.
    """
    h = horizon_h * horizon_days
    label = f"S3_winter{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running S3 — Winter ({horizon_days}d) ...")
    cfg = copy.deepcopy(DEFAULT_NODE_CFG)
    for nid in cfg:
        if "avg_load_kw" in cfg[nid]:
            cfg[nid]["avg_load_kw"] *= 1.10
    params = SEASONAL_PARAMS["winter"]
    load_df, pv_df = _make_profiles(
        cfg, dt_min=dt_min, horizon_h=h,
        irradiance_scale=params["irradiance_scale"],
        weather_season=params["weather"],
        start=params["start"],
    )
    return _run_policy_suite(label, load_df, pv_df, cfg, approach, out_dir, save)


# ---------------------------------------------------------------------------
# S4 — Scaling: 3 → 10 → 21 houses
# ---------------------------------------------------------------------------

def run_s4(
    house_counts: list[int] | None = None,
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """S4: scale community from 3 to 10 to 21 houses.

    Extra houses are clones of H1 config (4 kW PV, 10 kWh battery, 1.5 kW avg load).
    Returns a dict keyed by '{n_houses}_{policy_name}'.
    Runs both greedy and community policies for each house count.
    """
    if house_counts is None:
        house_counts = [3, 10, 21]
    print(f"Running S4 — Scaling {house_counts} houses (greedy + community) ...")

    scale_results: dict[str, tuple[SimResults, Metrics]] = {}

    base_cfg = copy.deepcopy(DEFAULT_NODE_CFG)   # has M, S, H1, H2, H3 = 3 houses

    for n_houses in house_counts:
        cfg = copy.deepcopy(base_cfg)
        # Add extra house nodes beyond H3
        for i in range(n_houses - 3):
            nid = f"H{4 + i}"
            cfg[nid] = {
                "has_grid_port": False,
                "pv_kw": 4.0,
                "battery_kwh": 10.0,
                "avg_load_kw": 1.5,
            }

        load_df, pv_df = _make_profiles(
            cfg, dt_min=dt_min, horizon_h=horizon_h,
            irradiance_scale=1.0, start="2024-06-21",
            seed=42 + n_houses,
        )

        for pol_name, pol_cls in [("greedy", GreedyPolicy), ("community", CommunityGreedyPolicy)]:
            policy = pol_cls()
            results = run_simulation(load_df, pv_df, policy=policy, node_cfg=cfg,
                                     approach=approach, policy_name=pol_name)
            metrics = compute_metrics(results)
            print(f"  {n_houses:>3} houses [{pol_name:<9}]: "
                  f"SS={metrics.self_sufficiency_pct:.1f}%  "
                  f"grid_import={metrics.grid_import_kwh:.1f} kWh")
            scale_results[f"{n_houses}_{pol_name}"] = (results, metrics)
            if save:
                save_results(results, metrics, out_dir, f"S4_{n_houses}houses_{pol_name}")

    return scale_results


# ---------------------------------------------------------------------------
# S5 — Battery sizing sweep
# ---------------------------------------------------------------------------

def run_s5(
    battery_sizes_kwh: list[float] | None = None,
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """S5: marginal self-sufficiency per added kWh of battery.

    Sweeps total battery capacity (all nodes scaled proportionally).
    Runs both greedy and community policies so the policy gap is visible
    at each battery size.  Returns dict keyed '{kwh}_{policy}'.
    """
    if battery_sizes_kwh is None:
        battery_sizes_kwh = [0, 10, 20, 40, 60, 80.5, 100]  # total across H1+H2+H3+S

    print("Running S5 — Battery sizing sweep (greedy + community) ...")
    base_total = sum(
        DEFAULT_NODE_CFG[n].get("battery_kwh", 0.0) for n in DEFAULT_NODE_CFG
    )  # 10 + 7 + 13.5 + 50 = 80.5 kWh default

    sweep_results: dict[str, tuple[SimResults, Metrics]] = {}

    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=horizon_h,
        irradiance_scale=1.0, start="2024-06-21",
    )

    print(f"\n  {'kWh':>6}  {'greedy SS%':>11}  {'community SS%':>14}")
    print("  " + "-" * 36)

    for total_kwh in battery_sizes_kwh:
        scale = total_kwh / base_total if base_total > 0 else 0.0
        cfg = copy.deepcopy(DEFAULT_NODE_CFG)
        for nid in cfg:
            if cfg[nid].get("battery_kwh", 0.0) > 0:
                cfg[nid]["battery_kwh"] = cfg[nid]["battery_kwh"] * scale

        row_ss: dict[str, float] = {}
        for pol_name, pol_cls in [("greedy", GreedyPolicy), ("community", CommunityGreedyPolicy)]:
            policy = pol_cls()
            results = run_simulation(load_df, pv_df, policy=policy, node_cfg=cfg,
                                     approach=approach, policy_name=pol_name)
            metrics = compute_metrics(results)
            sweep_results[f"{total_kwh}_{pol_name}"] = (results, metrics)
            row_ss[pol_name] = metrics.self_sufficiency_pct

        print(f"  {total_kwh:>6.1f}  {row_ss['greedy']:>10.1f}%  {row_ss['community']:>13.1f}%")

    if save:
        rows = []
        for key, (_, m) in sweep_results.items():
            total_kwh_str, pol_name = key.rsplit("_", 1)
            d = m.to_dict()
            d["total_battery_kwh"] = float(total_kwh_str)
            d["policy"] = pol_name
            rows.append(d)
        pd.DataFrame(rows).to_csv(
            _ensure_dir(out_dir) / "S5_battery_sweep.csv", index=False
        )

    print()
    return sweep_results


# ---------------------------------------------------------------------------
# S6 — Greedy vs predictive policy
# ---------------------------------------------------------------------------

def run_s6(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """S6: compare four dispatch policies on the same sunny day.

    Policies:
      greedy              — per-node local-first (baseline)
      predictive          — original rule-based look-ahead (reference, was underperforming)
      community           — greedy + S's battery serves community night deficit
      community_predictive — community + pre-dawn pre-discharge of S's battery
    """
    print("Running S6 — Policy comparison (4 policies) ...")

    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=horizon_h,
        irradiance_scale=1.0, start="2024-06-21",
    )

    policy_results: dict[str, tuple[SimResults, Metrics]] = {}

    policies: list[tuple[str, object]] = [
        ("greedy",               GreedyPolicy()),
        ("predictive",           PredictivePolicy()),
        ("community",            CommunityGreedyPolicy()),
        ("community_predictive", CommunityPredictivePolicy()),
    ]

    header_printed = False
    for name, policy in policies:
        results = run_simulation(load_df, pv_df, policy=policy,
                                 approach=approach, policy_name=name)
        metrics = compute_metrics(results)

        if not header_printed:
            print(f"\n  {'Policy':<22} {'SS%':>6} {'SC%':>6} {'Grid import':>12} "
                  f"{'Curtail':>10} {'Port loss':>10} {'Cable loss':>11}")
            print("  " + "-" * 82)
            header_printed = True

        m = metrics
        print(
            f"  {name:<22} "
            f"{m.self_sufficiency_pct:>5.1f}% "
            f"{m.self_consumption_pct:>5.1f}% "
            f"{m.grid_import_kwh:>11.2f} kWh "
            f"{m.curtailed_kwh:>9.2f} kWh "
            f"{m.total_port_loss_kwh:>9.2f} kWh "
            f"{m.total_cable_loss_kwh:>10.2f} kWh"
        )

        policy_results[name] = (results, metrics)
        if save:
            save_results(results, metrics, out_dir, f"S6_{name}")

    print()
    return policy_results


# ---------------------------------------------------------------------------
# S7 — Islanded / grid outage
# ---------------------------------------------------------------------------

def run_s7(
    outage_start_h: float = 10.0,
    outage_duration_h: float = 6.0,
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> tuple[SimResults, Metrics]:
    """S7: grid outage for outage_duration_h hours starting at outage_start_h.

    For multi-day runs (horizon_days > 1) the outage recurs on every day at
    the same local time.  Battery SoC carries over between days.

    Islanding is implemented via Option B: pandapower still converges with the
    VSC present, but during outage steps grid exchange is clamped to zero.
    Any unmet import is recorded as shed_load_kw (unserved demand).
    """
    h = horizon_h * horizon_days
    dt_h = dt_min / 60.0
    steps_per_day = round(24.0 / dt_h)
    label = f"S7_islanded{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"

    print(f"Running S7 — Islanded ({horizon_days}d, outage "
          f"{outage_start_h:.1f}h–{outage_start_h + outage_duration_h:.1f}h each day) ...")

    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=h,
        irradiance_scale=1.0, weather_season="summer_sunny",
        start="2024-06-21",
    )

    # Outage recurs on every day at the same local time window
    n_steps = int(h / dt_h)
    outage_steps: set[int] = set()
    for i in range(n_steps):
        local_h = (i % steps_per_day) * dt_h
        if outage_start_h <= local_h < outage_start_h + outage_duration_h:
            outage_steps.add(i)

    policy = CommunityGreedyPolicy()
    results = run_simulation(
        load_df, pv_df,
        policy=policy,
        approach=approach,
        policy_name="islanded_community",
        grid_export_limit_kw=5.0,
        grid_outage_steps=outage_steps,
    )
    metrics = compute_metrics(results)
    print(metrics.summary())
    print(f"  Outage window/day     : {outage_start_h:.1f}h–"
          f"{outage_start_h + outage_duration_h:.1f}h  "
          f"({len(outage_steps)} total steps × {dt_min} min)")
    if save:
        save_results(results, metrics, out_dir, label)
    return results, metrics


# ---------------------------------------------------------------------------
# T4 — Ring topology
# ---------------------------------------------------------------------------

def run_t4_ring(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """T4: H3→H1 ring closure added to the baseline star.

    The ring provides a second path between houses so H3 can reach H1
    without traversing M.  Question: does the shorter hop reduce cable
    losses and improve voltage profiles compared to T1?
    Runs greedy and community policies.
    """
    h = horizon_h * horizon_days
    label = f"T4_ring{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running T4 — Ring topology ({horizon_days}d) ...")
    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=h,
        irradiance_scale=1.0, weather_season="summer_sunny",
        start="2024-06-21",
    )
    results = {}
    for pol_name, pol_cls in [("greedy", GreedyPolicy), ("community", CommunityGreedyPolicy)]:
        r = run_simulation(
            load_df, pv_df,
            policy=pol_cls(),
            node_cfg=DEFAULT_NODE_CFG,
            approach=approach,
            policy_name=pol_name,
            topology_cfg=T4_RING,
        )
        m = compute_metrics(r)
        results[pol_name] = (r, m)
        if save:
            save_results(r, m, out_dir, f"{label}_{pol_name}")

    _print_topology_summary("T4 Ring", results)
    return results


# ---------------------------------------------------------------------------
# T_S_SPOKE — Farm directly wired to each house
# ---------------------------------------------------------------------------

def run_t_s_spoke(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """T_S_SPOKE: farm S wired directly to each house (in addition to star).

    Farm battery energy reaches houses in one cable hop (S→Hx) rather than
    two (S→M→Hx), cutting cable loss on farm discharge.  Intended to show
    whether the community policy leverages the shorter path.
    """
    h = horizon_h * horizon_days
    label = f"T_S_spoke{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running T_S_SPOKE — S-spoke topology ({horizon_days}d) ...")
    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=h,
        irradiance_scale=1.0, weather_season="summer_sunny",
        start="2024-06-21",
    )
    results = {}
    for pol_name, pol_cls in [("greedy", GreedyPolicy), ("community", CommunityGreedyPolicy)]:
        r = run_simulation(
            load_df, pv_df,
            policy=pol_cls(),
            node_cfg=DEFAULT_NODE_CFG,
            approach=approach,
            policy_name=pol_name,
            topology_cfg=T_S_SPOKE,
        )
        m = compute_metrics(r)
        results[pol_name] = (r, m)
        if save:
            save_results(r, m, out_dir, f"{label}_{pol_name}")

    _print_topology_summary("T_S_SPOKE", results)
    return results


# ---------------------------------------------------------------------------
# T_DC_BUS — Ring main (no central hub)
# ---------------------------------------------------------------------------

def run_t_dc_bus(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """T_DC_BUS: all nodes on a shared ring main; M is just one tap.

    Represents a single-conductor DC backbone where no node acts as hub.
    Shows how loss changes when flows don't converge through M.
    """
    h = horizon_h * horizon_days
    label = f"T_DC_bus{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running T_DC_BUS — Ring main topology ({horizon_days}d) ...")
    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=h,
        irradiance_scale=1.0, weather_season="summer_sunny",
        start="2024-06-21",
    )
    results = {}
    for pol_name, pol_cls in [("greedy", GreedyPolicy), ("community", CommunityGreedyPolicy)]:
        r = run_simulation(
            load_df, pv_df,
            policy=pol_cls(),
            node_cfg=DEFAULT_NODE_CFG,
            approach=approach,
            policy_name=pol_name,
            topology_cfg=T_DC_BUS,
        )
        m = compute_metrics(r)
        results[pol_name] = (r, m)
        if save:
            save_results(r, m, out_dir, f"{label}_{pol_name}")

    _print_topology_summary("T_DC_BUS", results)
    return results


# ---------------------------------------------------------------------------
# T5 — EV as dispatchable V2G storage
# ---------------------------------------------------------------------------

# EV node config: H1 gets a 30 kWh EV battery (bidirectional 7 kW charger)
_EV_NODE_CFG: dict[str, dict] = {
    **{k: copy.deepcopy(v) for k, v in DEFAULT_NODE_CFG.items()},
}
_EV_NODE_CFG["H1"]["ev_battery_kwh"]     = 30.0
_EV_NODE_CFG["H1"]["ev_min_soc_pct"]     = 20.0
_EV_NODE_CFG["H1"]["ev_initial_soc_pct"] = 60.0
_EV_NODE_CFG["H1"]["ev_depart_h"]        = 8.0    # departs 8 AM
_EV_NODE_CFG["H1"]["ev_return_h"]        = 18.0   # returns 6 PM
_EV_NODE_CFG["H1"]["ev_p_max_kw"]        = 7.0


def run_t5_ev(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """T5: H1 has a bidirectional EV (30 kWh, 7 kW V2G).

    The EVAwareCommunityPolicy charges the EV with surplus solar, then
    discharges it (V2G) during evening deficit, with a hard-charge window
    before 8 AM departure to ensure 80% SoC for the commute.

    Compares community-greedy (no EV dispatch) vs ev_aware (V2G active)
    to isolate the EV contribution.
    """
    h = horizon_h * horizon_days
    label = f"T5_ev_v2g{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running T5 — EV V2G scenario ({horizon_days}d) ...")
    load_df, pv_df = _make_profiles(
        _EV_NODE_CFG, dt_min=dt_min, horizon_h=h,
        irradiance_scale=1.0, weather_season="summer_sunny",
        start="2024-06-21",
    )

    results = {}
    policies: list[tuple[str, object]] = [
        ("community",  CommunityGreedyPolicy()),
        ("ev_aware",   EVAwareCommunityPolicy(
            depart_h=8.0, return_h=18.0,
            depart_soc_pct=80.0, min_ev_soc_pct=20.0,
        )),
    ]
    for pol_name, policy in policies:
        r = run_simulation(
            load_df, pv_df,
            policy=policy,
            node_cfg=_EV_NODE_CFG,
            approach=approach,
            policy_name=pol_name,
            topology_cfg=T1_STAR,
        )
        m = compute_metrics(r)
        results[pol_name] = (r, m)
        if save:
            save_results(r, m, out_dir, f"{label}_{pol_name}")

    _print_topology_summary("T5 EV V2G", results)
    return results


# ---------------------------------------------------------------------------
# T6 — Apartment block (shared rooftop, 21 units)
# ---------------------------------------------------------------------------

def _build_apartment_cfg(n_apartments: int = 21) -> dict[str, dict]:
    """Build node_cfg for an apartment block.

    Layout:
      M  — building main bus (grid connection, no load, no PV)
      S  — shared rooftop farm (large PV + large battery)
      H1..H{n_apartments} — apartment units (load only, no local PV or battery)

    PV and battery are scaled 7× from the default 3-house config to supply
    21 units.  Each apartment has ~1.4 kW average load (slightly lower than
    the semi-urban house because apartment electricity use is more homogeneous).
    """
    pv_scale = n_apartments / 3
    cfg: dict[str, dict] = {
        "M": {"has_grid_port": True, "pv_kw": 0.0, "battery_kwh": 0.0, "avg_load_kw": 0.0},
        "S": {
            "has_grid_port": False,
            "pv_kw": 20.0 * pv_scale,          # ~140 kW rooftop solar for 21 units
            "battery_kwh": 50.0 * pv_scale,     # ~350 kWh shared BESS
            "avg_load_kw": 0.0,
        },
    }
    # First 3 apartments reuse H1/H2/H3 node IDs (base topology buses)
    apt_names = [f"H{i+1}" for i in range(n_apartments)]
    for apt in apt_names:
        cfg[apt] = {
            "has_grid_port": False,
            "pv_kw": 0.0,
            "battery_kwh": 0.0,
            "avg_load_kw": 1.4,
        }
    return cfg


# T6 uses T1_STAR-style cables for H1/H2/H3 (short-run, same building),
# plus auto-generated 10m spokes to extra apartments.
T6_STAR_SHORT = TopologyConfig(
    name="T6_star_short",
    cables=[
        ("M", "S",  0.010),
        ("M", "H1", 0.010),
        ("M", "H2", 0.010),
        ("M", "H3", 0.010),
    ],
    extra_house_cable_length_km=0.010,
    description="Apartment block: all units on 10m internal runs from building bus.",
)


def run_t6_apartment(
    n_apartments: int = 21,
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """T6: apartment block — shared rooftop PV/BESS, 21 apartments, no local PV.

    Virtual Net Metering billing model: the shared farm credits each apartment
    proportionally.  From simulation physics: all apartments draw from the
    shared DC bus, which is fed by the rooftop S node.

    Grid export limit is raised (net metering under VNM/GNM rules typically
    allows larger export from a group system) — modelled as 20 kW cap.
    """
    h = horizon_h * horizon_days
    label = f"T6_apartment_{n_apartments}apt{'_' + str(horizon_days) + 'd' if horizon_days > 1 else ''}"
    print(f"Running T6 — Apartment block ({n_apartments} apartments, {horizon_days}d) ...")

    apt_cfg = _build_apartment_cfg(n_apartments)
    load_df, pv_df = _make_profiles(
        apt_cfg, dt_min=dt_min, horizon_h=h,
        irradiance_scale=1.0, weather_season="summer_sunny",
        start="2024-06-21",
        seed=99,
    )

    results = {}
    for pol_name, pol_cls in [("greedy", GreedyPolicy), ("community", CommunityGreedyPolicy)]:
        r = run_simulation(
            load_df, pv_df,
            policy=pol_cls(),
            node_cfg=apt_cfg,
            approach=approach,
            policy_name=pol_name,
            grid_export_limit_kw=20.0,   # VNM group metering allows larger export cap
            topology_cfg=T6_STAR_SHORT,
        )
        m = compute_metrics(r)
        results[pol_name] = (r, m)
        if save:
            save_results(r, m, out_dir, f"{label}_{pol_name}")

    _print_topology_summary(f"T6 Apartment ({n_apartments} units)", results)
    return results


# ---------------------------------------------------------------------------
# Grid export cap sweep (replaces standalone T2)
# ---------------------------------------------------------------------------

def run_grid_cap_sweep(
    export_caps_kw: list[float] | None = None,
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """Sweep the grid export limit (kW) on the baseline T1 topology.

    This captures the energy impact of T2 (distributed grid ties) via a
    parameter sweep rather than a separate physical topology.  Regimes:
      5 kW  → current baseline (single small grid connection at M)
      15 kW → larger inverter at M (still single point)
      50 kW → effectively unconstrained for a 3-house community
      0 kW  → zero-export (DISCOM policy common in India)

    Returns dict keyed '{cap}kW_{policy}'.
    """
    if export_caps_kw is None:
        export_caps_kw = [0.0, 5.0, 10.0, 20.0, 50.0]

    print("Running Grid Export Cap Sweep ...")
    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=horizon_h,
        irradiance_scale=1.0, start="2024-06-21",
    )

    print(f"\n  {'Cap (kW)':>10}  {'Policy':<18}  {'SS%':>6}  {'SC%':>6}  "
          f"{'Export kWh':>11}  {'Curtail kWh':>12}")
    print("  " + "-" * 72)

    sweep_results: dict[str, tuple[SimResults, Metrics]] = {}
    for cap in export_caps_kw:
        for pol_name, pol_cls in [("community", CommunityGreedyPolicy)]:
            r = run_simulation(
                load_df, pv_df,
                policy=pol_cls(),
                node_cfg=DEFAULT_NODE_CFG,
                approach=approach,
                policy_name=pol_name,
                grid_export_limit_kw=cap,
            )
            m = compute_metrics(r)
            key = f"{cap:.0f}kW_{pol_name}"
            sweep_results[key] = (r, m)
            print(
                f"  {cap:>10.0f}  {pol_name:<18}  "
                f"{m.self_sufficiency_pct:>5.1f}%  "
                f"{m.self_consumption_pct:>5.1f}%  "
                f"{m.grid_export_kwh:>10.2f} kWh  "
                f"{m.curtailed_kwh:>11.2f} kWh"
            )

    if save:
        rows = []
        for key, (_, m) in sweep_results.items():
            cap_str, pol = key.rsplit("_", 1)
            d = m.to_dict()
            d["export_cap_kw"] = float(cap_str.replace("kW", ""))
            d["policy"] = pol
            rows.append(d)
        pd.DataFrame(rows).to_csv(
            _ensure_dir(out_dir) / "T2_grid_cap_sweep.csv", index=False
        )

    print()
    return sweep_results


# ---------------------------------------------------------------------------
# Topology comparison (all physical topologies, community policy, 1 sunny day)
# ---------------------------------------------------------------------------

def run_topology_comparison(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
    save: bool = True,
) -> dict[str, tuple[SimResults, Metrics]]:
    """Run all physical topologies on the same profiles with community policy.

    Provides a direct like-for-like comparison of the cable layouts:
      T1_star, T4_ring, T_S_spoke, T_DC_bus

    Same node_cfg, same load/PV profiles, same community policy — only
    the cable layout changes.  Highlights topology-driven differences in
    cable losses, voltage profiles, and self-sufficiency.
    Set horizon_days > 1 for multi-day runs (SoC carries over per topology).
    """
    h = horizon_h * horizon_days
    sfx = f"_{horizon_days}d" if horizon_days > 1 else ""
    print(f"Running Topology Comparison — T1/T4/T_S_spoke/T_DC_bus ({horizon_days}d) ...")

    load_df, pv_df = _make_profiles(
        DEFAULT_NODE_CFG, dt_min=dt_min, horizon_h=h,
        irradiance_scale=1.0, weather_season="summer_sunny",
        start="2024-06-21",
    )

    topology_variants = [
        ("T1_star",    T1_STAR),
        ("T4_ring",    T4_RING),
        ("T_S_spoke",  T_S_SPOKE),
        ("T_DC_bus",   T_DC_BUS),
    ]

    print(f"\n  {'Topology':<14} {'SS%':>6} {'SC%':>6} {'Grid import':>12} "
          f"{'Curtail':>10} {'Cable loss':>11} {'Min Vm (pu)':>12}")
    print("  " + "-" * 78)

    comparison: dict[str, tuple[SimResults, Metrics]] = {}
    for tname, tcfg in topology_variants:
        r = run_simulation(
            load_df, pv_df,
            policy=CommunityGreedyPolicy(),
            node_cfg=DEFAULT_NODE_CFG,
            approach=approach,
            policy_name="community",
            topology_cfg=tcfg,
        )
        m = compute_metrics(r)

        # Min voltage across all nodes over all steps
        df = r.to_dataframe()
        vm_cols = [c for c in df.columns if c.endswith("_vm_pu")]
        min_vm = df[vm_cols].min().min() if vm_cols else float("nan")

        comparison[tname] = (r, m)
        if save:
            save_results(r, m, out_dir, f"Tcomp_{tname}{sfx}")

        print(
            f"  {tname:<14} "
            f"{m.self_sufficiency_pct:>5.1f}% "
            f"{m.self_consumption_pct:>5.1f}% "
            f"{m.grid_import_kwh:>11.2f} kWh "
            f"{m.curtailed_kwh:>9.2f} kWh "
            f"{m.total_cable_loss_kwh:>10.3f} kWh "
            f"{min_vm:>11.4f} pu"
        )

    print()
    return comparison


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_topology_summary(label: str, results: dict[str, tuple[SimResults, Metrics]]) -> None:
    print(f"\n  {label}")
    print(f"  {'Policy':<22} {'SS%':>6} {'SC%':>6} {'Grid import':>12} "
          f"{'Curtail':>10} {'Cable loss':>11}")
    print("  " + "-" * 74)
    for pol_name, (_, m) in results.items():
        print(
            f"  {pol_name:<22} "
            f"{m.self_sufficiency_pct:>5.1f}% "
            f"{m.self_consumption_pct:>5.1f}% "
            f"{m.grid_import_kwh:>11.2f} kWh "
            f"{m.curtailed_kwh:>9.2f} kWh "
            f"{m.total_cable_loss_kwh:>10.3f} kWh"
        )
    print()


# ---------------------------------------------------------------------------
# Run all (convenience)
# ---------------------------------------------------------------------------

def run_all(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    approach: str = "dc",
) -> dict[str, Any]:
    """Run S0–S7 and return all results."""
    all_results: dict[str, Any] = {}
    all_results["S0"] = run_s0(out_dir, dt_min, horizon_h, approach)
    all_results["S1"] = run_s1(out_dir, dt_min, horizon_h, approach)
    all_results["S2"] = run_s2(out_dir, dt_min, horizon_h, approach)
    all_results["S3"] = run_s3(out_dir, dt_min, horizon_h, approach)
    all_results["S4"] = run_s4(out_dir=out_dir, dt_min=dt_min,
                                horizon_h=horizon_h, approach=approach)
    all_results["S5"] = run_s5(out_dir=out_dir, dt_min=dt_min,
                                horizon_h=horizon_h, approach=approach)
    all_results["S6"] = run_s6(out_dir, dt_min, horizon_h, approach)
    all_results["S7"] = run_s7(out_dir=out_dir, dt_min=dt_min,
                                horizon_h=horizon_h, approach=approach)
    return all_results


def run_all_topologies(
    out_dir: str | Path = "data/results",
    dt_min: int = 15,
    horizon_h: float = 24.0,
    horizon_days: int = 1,
    approach: str = "dc",
) -> dict[str, Any]:
    """Run all topology scenarios and the grid-cap sweep.

    Set horizon_days > 1 for multi-day runs (SoC carries over, daily
    irradiance varies).  grid_cap_sweep always uses 1-day (parameter sweep).
    """
    all_results: dict[str, Any] = {}
    all_results["topology_comparison"] = run_topology_comparison(
        out_dir, dt_min, horizon_h, horizon_days, approach)
    all_results["T4_ring"]    = run_t4_ring(
        out_dir, dt_min, horizon_h, horizon_days, approach=approach)
    all_results["T_S_spoke"]  = run_t_s_spoke(
        out_dir, dt_min, horizon_h, horizon_days, approach=approach)
    all_results["T_DC_bus"]   = run_t_dc_bus(
        out_dir, dt_min, horizon_h, horizon_days, approach=approach)
    all_results["T5_ev"]      = run_t5_ev(
        out_dir, dt_min, horizon_h, horizon_days, approach=approach)
    all_results["T6_apartment"] = run_t6_apartment(
        out_dir=out_dir, dt_min=dt_min, horizon_h=horizon_h,
        horizon_days=horizon_days, approach=approach)
    all_results["grid_cap_sweep"] = run_grid_cap_sweep(
        out_dir=out_dir, dt_min=dt_min, horizon_h=horizon_h, approach=approach)
    return all_results
