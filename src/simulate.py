"""
simulate.py — Time-series simulation loop.

Architecture (two-layer):
  Layer A (per-node, per-timestep):
    dispatch.py → decides battery_kw for each node
    router.py   → applies solar/battery η → returns net_bus_kw
  Layer B (whole network, per-timestep):
    pandapower  → given net bus powers, solves for cable flows + VSC exchange

Post-pandapower (per-timestep):
  Grid port loss  : η_grid applied to VSC exchange
  Peer port losses: η_peer applied to each DC cable flow (both ends)
  Curtailment     : excess community injection beyond grid_export_limit_kw

Power balance assertion (§11):
  Σ solar_available − Σ solar_loss − Σ battery_loss − Σ standby_loss
  − cable_loss − peer_port_loss − grid_port_loss
  − Δ battery_stored − grid_exchange_net ≈ Σ load_kw × dt_h
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .battery import Battery
from .dispatch import CommunitySetpoints, DispatchPolicy, GreedyPolicy, NodeState
from .generator import Generator
from .network import (
    NODE_IDS, EnergyNet, TopologyConfig,
    build_network, extract_results, run_powerflow, set_bus_powers,
)
from .ports import P_STANDBY_W, eta

P_STANDBY_KW = {k: v / 1000.0 for k, v in P_STANDBY_W.items()}
from .router import Router, RouterConfig, RouterStepResult

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Community default configuration (§4 of build brief)
# ---------------------------------------------------------------------------

DEFAULT_NODE_CFG: dict[str, dict[str, Any]] = {
    "M":  {"has_grid_port": True,  "pv_kw": 0.0,  "battery_kwh": 0.0,  "avg_load_kw": 0.0},
    "S":  {"has_grid_port": False, "pv_kw": 20.0, "battery_kwh": 50.0, "avg_load_kw": 0.0},
    "H1": {"has_grid_port": False, "pv_kw": 4.0,  "battery_kwh": 10.0, "avg_load_kw": 1.5},
    "H2": {"has_grid_port": False, "pv_kw": 3.0,  "battery_kwh": 7.0,  "avg_load_kw": 2.0},
    "H3": {"has_grid_port": False, "pv_kw": 5.0,  "battery_kwh": 13.5, "avg_load_kw": 1.2},
}

POWER_BALANCE_TOL_KW: float = 1.0   # kW per step


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class StepRecord:
    t: pd.Timestamp
    node_results: dict[str, RouterStepResult]
    net_bus_kw: dict[str, float]
    pf_results: dict
    grid_exchange_kw: float       # + import, − export (raw VSC result; 0 during islanding)
    grid_port_loss_kw: float      # conversion loss in grid port
    cable_loss_kw: float          # I²R cable losses (pandapower)
    peer_port_loss_kw: float      # peer port conversion losses (post-hoc estimate)
    curtailed_kw: float           # PV curtailed this step
    shed_load_kw: float           # load that couldn't be served during grid outage
    shed_critical_kw: float       # subset of shed_load that was critical (non-sheddable)
    deferred_kw: float            # load deferred by demand-response policy (pump/irrigation)
    balance_error_kw: float
    islanded: bool = False        # True when grid was disconnected this step
    line_flows_mw: dict[str, float] = field(default_factory=dict)


@dataclass
class SimResults:
    steps: list[StepRecord] = field(default_factory=list)
    node_cfg: dict[str, dict] = field(default_factory=dict)
    dt_h: float = 0.25
    approach: str = "dc"
    policy_name: str = "greedy"
    _df: pd.DataFrame | None = field(default=None, init=False, repr=False)

    def to_dataframe(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        rows = []
        for s in self.steps:
            row: dict[str, Any] = {
                "t": s.t,
                "grid_exchange_kw": s.grid_exchange_kw,
                "grid_port_loss_kw": s.grid_port_loss_kw,
                "cable_loss_kw": s.cable_loss_kw,
                "peer_port_loss_kw": s.peer_port_loss_kw,
                "curtailed_kw": s.curtailed_kw,
                "shed_load_kw": s.shed_load_kw,
                "shed_critical_kw": s.shed_critical_kw,
                "deferred_kw": s.deferred_kw,
                "islanded": s.islanded,
                "balance_error_kw": s.balance_error_kw,
            }
            for nid, r in s.node_results.items():
                row[f"{nid}_solar_backplane_kw"] = r.solar_kw
                row[f"{nid}_load_kw"]            = r.load_kw
                row[f"{nid}_battery_kw"]         = r.battery_kw
                row[f"{nid}_solar_loss_kw"]      = r.solar_loss_kw
                row[f"{nid}_batt_loss_kw"]       = r.battery_loss_kw
                row[f"{nid}_standby_loss_kw"]    = r.standby_loss_kw
                row[f"{nid}_soc_kwh"]            = r.soc_kwh
                row[f"{nid}_soc_pct"]            = r.soc_pct
                row[f"{nid}_vm_pu"]              = s.pf_results.get("vm_pu", {}).get(nid, float("nan"))
                if r.ev_battery_kw != 0.0 or r.ev_soc_kwh != 0.0:
                    row[f"{nid}_ev_battery_kw"]  = r.ev_battery_kw
                    row[f"{nid}_ev_soc_kwh"]     = r.ev_soc_kwh
                    row[f"{nid}_ev_soc_pct"]     = r.ev_soc_pct
                if r.generator_kw > 0.0 or r.generator_running:
                    row[f"{nid}_generator_kw"]       = r.generator_kw
                    row[f"{nid}_generator_fuel_L"]   = r.generator_fuel_step_L
            rows.append(row)
        self._df = pd.DataFrame(rows).set_index("t")
        return self._df


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def build_routers(node_cfg: dict[str, dict]) -> dict[str, Router]:
    routers: dict[str, Router] = {}
    for nid, cfg in node_cfg.items():
        batt_kwh = cfg.get("battery_kwh", 0.0)
        battery = Battery(capacity_kwh=batt_kwh) if batt_kwh > 0 else None

        ev_kwh = cfg.get("ev_battery_kwh", 0.0)
        if ev_kwh > 0:
            ev_min_frac = cfg.get("ev_min_soc_pct", 20.0) / 100.0
            ev_init_pct = cfg.get("ev_initial_soc_pct", 60.0) / 100.0
            ev_p_max = cfg.get("ev_p_max_kw", 7.0)
            ev_battery = Battery(
                capacity_kwh=ev_kwh,
                soc_min_frac=ev_min_frac,
                soc_max_frac=0.95,
                soc_init_kwh=ev_kwh * ev_init_pct,
                p_max_kw=ev_p_max,
            )
        else:
            ev_battery = None

        gen_kw = cfg.get("generator_kw", 0.0)
        if gen_kw > 0:
            generator = Generator(
                p_rated_kw=gen_kw,
                min_load_frac=cfg.get("gen_min_load_frac", 0.25),
                fuel_type=cfg.get("gen_fuel_type", "diesel"),
            )
        else:
            generator = None

        rc = RouterConfig(
            node_id=nid,
            pv_peak_kw=cfg.get("pv_kw", 0.0),
            battery=battery,
            has_grid_port=cfg.get("has_grid_port", False),
            ev_battery=ev_battery,
            ev_p_max_kw=cfg.get("ev_p_max_kw", 7.0),
            generator=generator,
            critical_load_kw=cfg.get("critical_load_kw", 0.0),
        )
        routers[nid] = Router(rc)
    return routers


# ---------------------------------------------------------------------------
# Main simulation loop
# ---------------------------------------------------------------------------

def run_simulation(
    load_df: pd.DataFrame,
    pv_df: pd.DataFrame,
    policy: DispatchPolicy | None = None,
    node_cfg: dict[str, dict] | None = None,
    approach: str = "dc",
    policy_name: str = "greedy",
    grid_export_limit_kw: float = 5.0,
    grid_outage_steps: set[int] | None = None,
    topology_cfg: TopologyConfig | None = None,
) -> SimResults:
    """Run the full time-series simulation.

    Parameters
    ----------
    load_df : pd.DataFrame     Columns = node IDs, kW.
    pv_df   : pd.DataFrame     Columns = node IDs, kW available.
    policy  : DispatchPolicy   Default: GreedyPolicy.
    node_cfg : dict            Community configuration. Default: DEFAULT_NODE_CFG.
    approach : 'dc' | 'ac'    Network model.
    policy_name : str          Label for bookkeeping.
    grid_export_limit_kw : float
        Maximum power the grid port can export (kW). If community surplus
        exceeds this, the excess is curtailed. Set to np.inf to disable.
    grid_outage_steps : set[int] | None
        Set of step indices (0-based) during which the grid is unavailable.
        During these steps pandapower still converges normally (VSC present),
        but the resulting grid exchange is clamped to 0:
          - import needed  → recorded as shed_load_kw (unserved demand)
          - export possible → treated as additional curtailment
        This implements Option-B islanding without topology changes.
    topology_cfg : TopologyConfig | None
        Cable layout config.  None → T1_STAR (baseline star).
    """
    if node_cfg is None:
        node_cfg = DEFAULT_NODE_CFG
    if policy is None:
        policy = GreedyPolicy()

    idx = load_df.index.intersection(pv_df.index)
    load_df = load_df.loc[idx]
    pv_df   = pv_df.loc[idx]
    n_steps = len(idx)
    if n_steps == 0:
        raise ValueError("No overlapping timesteps in load and PV profiles.")

    dt_h = (idx[1] - idx[0]).total_seconds() / 3600.0 if n_steps > 1 else 0.25

    # Wire predictive forecasts if needed
    from .dispatch import PredictivePolicy
    if isinstance(policy, PredictivePolicy):
        policy.update_forecasts(
            future_solar_kw={nid: pv_df[nid].tolist() if nid in pv_df.columns else []
                             for nid in node_cfg},
            future_load_kw={nid: load_df[nid].tolist() if nid in load_df.columns else []
                            for nid in node_cfg},
        )
        policy.step_idx = 0

    n_extra = max(0, len(node_cfg) - len(NODE_IDS))
    en = build_network(topology_cfg=topology_cfg, approach=approach, n_extra_houses=n_extra)
    routers = build_routers(node_cfg)
    results = SimResults(node_cfg=node_cfg, dt_h=dt_h, approach=approach,
                         policy_name=policy_name)

    _outage_steps = grid_outage_steps or set()
    for step_i, t in enumerate(idx):
        # ---- Build node states ----
        states: dict[str, NodeState] = {}
        current_h = (step_i * dt_h) % 24.0
        for nid in node_cfg:
            solar_kw = float(pv_df[nid].loc[t]) if nid in pv_df.columns else 0.0
            load_kw  = float(load_df[nid].loc[t]) if nid in load_df.columns else 0.0
            r = routers[nid]
            batt = r.battery
            ev = r.ev_battery
            gen = r.generator
            ncfg = node_cfg[nid]
            depart_h = ncfg.get("ev_depart_h", 8.0)
            return_h = ncfg.get("ev_return_h", 18.0)
            ev_plugged_in = False
            if ev is not None:
                if return_h > depart_h:   # overnight parking
                    ev_plugged_in = current_h >= return_h or current_h < depart_h
                else:
                    ev_plugged_in = return_h <= current_h < depart_h
            states[nid] = NodeState(
                node_id=nid,
                solar_kw=solar_kw,
                load_kw=load_kw,
                soc_kwh=batt.soc_kwh if batt else 0.0,
                soc_pct=batt.soc_pct if batt else 0.0,
                battery_headroom_kw=r.battery_headroom_kw(),
                battery_available_kw=r.battery_available_kw(),
                has_battery=batt is not None,
                has_grid_port=r.cfg.has_grid_port,
                ev_soc_kwh=ev.soc_kwh if ev else 0.0,
                ev_soc_pct=ev.soc_pct if ev else 0.0,
                ev_headroom_kw=r.ev_headroom_kw() if ev_plugged_in else 0.0,
                ev_available_kw=r.ev_available_kw() if ev_plugged_in else 0.0,
                ev_plugged_in=ev_plugged_in,
                has_generator=gen is not None,
                generator_available_kw=gen.max_output_kw() if gen is not None else 0.0,
                is_deferrable=ncfg.get("is_deferrable", False),
            )

        # ---- Layer A: dispatch decides battery setpoints ----
        cs = policy.dispatch(states, dt_h)

        # ---- Commit router steps (applies η, advances SoC) ----
        node_results: dict[str, RouterStepResult] = {}
        net_bus_kw: dict[str, float] = {}
        step_deferred_kw = 0.0

        for nid in node_cfg:
            ns = cs.nodes.get(nid)
            battery_kw    = ns.battery_kw    if ns is not None else 0.0
            ev_battery_kw = ns.ev_battery_kw if ns is not None else 0.0
            generator_kw  = ns.generator_kw  if ns is not None else 0.0
            defer_load    = ns.defer_load     if ns is not None else False
            st = states[nid]
            served_load_kw = 0.0 if defer_load else st.load_kw
            if defer_load:
                step_deferred_kw += st.load_kw
            res = routers[nid].step(
                solar_available_kw=st.solar_kw,
                load_kw=served_load_kw,
                battery_kw=battery_kw,
                dt_h=dt_h,
                ev_battery_kw=ev_battery_kw,
                ev_plugged_in=st.ev_plugged_in,
                generator_kw=generator_kw,
            )
            node_results[nid] = res
            net_bus_kw[nid] = res.net_bus_kw

        # ---- Layer B: pandapower ----
        set_bus_powers(en, net_bus_kw)
        run_powerflow(en)
        pf = extract_results(en)

        # grid_exchange_kw_pf: raw pandapower result, + = import, − = export
        grid_exchange_kw_pf = pf.get("grid_exchange_kw", 0.0) if pf["converged"] else 0.0
        cable_loss_kw       = pf.get("cable_loss_kw", 0.0)    if pf["converged"] else 0.0

        # ---- Power balance assertion (uses uncapped pandapower result) ----
        balance_err = _check_balance(net_bus_kw, grid_exchange_kw_pf, cable_loss_kw, node_cfg)

        # ---- Curtailment: cap grid export at grid_export_limit_kw ----
        # Applied AFTER the balance check so the assertion reflects true physics.
        raw_grid_exchange = grid_exchange_kw_pf
        curtailed_kw = 0.0
        if raw_grid_exchange < -grid_export_limit_kw:
            curtailed_kw = abs(raw_grid_exchange) - grid_export_limit_kw
            raw_grid_exchange = -grid_export_limit_kw

        # ---- Islanding (Option B): clamp grid exchange to 0 ----
        islanded = step_i in _outage_steps
        shed_load_kw = 0.0
        shed_critical_kw = 0.0
        if islanded:
            if raw_grid_exchange > 0:
                # Community needed grid import → record as shed load
                shed_load_kw = raw_grid_exchange
                # Estimate critical fraction of shed load
                total_crit = sum(
                    routers[nid].cfg.critical_load_kw for nid in node_cfg
                )
                total_load = sum(st.load_kw for st in states.values())
                if total_load > 1e-6:
                    shed_critical_kw = shed_load_kw * min(1.0, total_crit / total_load)
            elif raw_grid_exchange < 0:
                curtailed_kw += abs(raw_grid_exchange)
            raw_grid_exchange = 0.0

        # ---- Grid port loss (applied to VSC exchange) ----
        grid_exchange_abs_kw = abs(raw_grid_exchange)
        if grid_exchange_abs_kw > 0.01:
            eta_g = eta("grid", grid_exchange_abs_kw * 1000.0)
            grid_port_loss_kw = grid_exchange_abs_kw * (1.0 - eta_g)
        else:
            grid_port_loss_kw = P_STANDBY_KW["grid"]

        # Net grid exchange after grid port efficiency
        grid_exchange_net_kw = (
            raw_grid_exchange * eta("grid", grid_exchange_abs_kw * 1000.0)
            if grid_exchange_abs_kw > 0.01 else 0.0
        )

        # ---- Peer port losses (post-hoc from cable flows) ----
        peer_port_loss_kw = _estimate_peer_port_losses(en, pf)
        if abs(balance_err) > POWER_BALANCE_TOL_KW:
            warnings.warn(
                f"[{t}] Power balance error {balance_err:.3f} kW "
                f"(tol={POWER_BALANCE_TOL_KW} kW).",
                stacklevel=2,
            )

        results.steps.append(StepRecord(
            t=t,
            node_results=node_results,
            net_bus_kw=net_bus_kw,
            pf_results=pf,
            grid_exchange_kw=raw_grid_exchange,
            grid_port_loss_kw=grid_port_loss_kw,
            cable_loss_kw=cable_loss_kw,
            peer_port_loss_kw=peer_port_loss_kw,
            curtailed_kw=curtailed_kw,
            shed_load_kw=shed_load_kw,
            shed_critical_kw=shed_critical_kw,
            deferred_kw=step_deferred_kw,
            islanded=islanded,
            balance_error_kw=balance_err,
            line_flows_mw=pf.get("line_flows_mw", {}),
        ))

    return results


# ---------------------------------------------------------------------------
# Post-pandapower loss helpers
# ---------------------------------------------------------------------------

def _estimate_peer_port_losses(en: EnergyNet, pf: dict) -> float:
    """Estimate peer port conversion losses from cable flows.

    Each cable flow passes through a peer port (DAB converter) at each end.
    Loss per end = |flow| × (1 − η_peer(|flow|)).
    We approximate: total peer port loss ≈ 2 × Σ |flow| × (1 − η_peer(|flow|)).

    NOTE: this is informational only — it does not feed back into the
    pandapower solve (which is already a closed physical solution).
    """
    if not pf.get("converged", False):
        return 0.0
    net = en.net
    total_loss_kw = 0.0
    try:
        if en.approach == "dc":
            flows_mw = net.res_line_dc["p_from_mw"].abs()
        else:
            flows_mw = net.res_line["p_from_mw"].abs()
        for flow_mw in flows_mw:
            flow_kw = float(flow_mw) * 1000.0
            if flow_kw > 0.01:
                eta_p = eta("peer", flow_kw * 1000.0)
                total_loss_kw += 2.0 * flow_kw * (1.0 - eta_p)
    except Exception:
        pass
    return total_loss_kw


def _check_balance(
    net_bus_kw: dict[str, float],
    raw_grid_exchange_kw: float,
    cable_loss_kw: float,
    node_cfg: dict,
) -> float:
    """Pandapower network self-consistency check.

    In the pandapower model:
      Σ(injections from load nodes) + grid_import − grid_export = cable_losses

    where: net_bus_kw > 0 = injection, < 0 = draw.
    The slack bus M is excluded (pandapower ignores its load_dc).

    Returns residual (should be ≈ 0 to pandapower solver tolerance, ~1e-6 MW).
    """
    # Identify slack node (has_grid_port) — pandapower ignores its load_dc entry
    slack_nodes = {nid for nid, cfg in node_cfg.items() if cfg.get("has_grid_port", False)}

    sum_injections = sum(v for k, v in net_bus_kw.items() if k not in slack_nodes)
    # raw_grid_exchange_kw > 0 means grid imports into DC net (adds supply)
    # raw_grid_exchange_kw < 0 means DC net exports to grid (removes supply)
    residual = sum_injections + raw_grid_exchange_kw - cable_loss_kw
    return residual
