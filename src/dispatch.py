"""
dispatch.py — Layer A dispatch policies.

What a policy decides (and does NOT decide):
  ✓ battery_kw per node — the only truly dispatchable element
  ✗ peer flows — these emerge naturally from pandapower's network solve
  ✗ grid exchange — the VSC slack in Layer B absorbs whatever the community
    can't balance locally; the grid port η is applied as a post-step metric

Why this separation is correct:
  The peer cables are resistive paths between DC buses. Given each node's net
  injection/draw, Kirchhoff's laws determine the actual cable flows — we cannot
  pre-specify "H1 exports exactly X kW to H2" without overriding physics.
  Attempting it caused the v0 curtailment / zero-export bug.

Policy 1 — Greedy "local-first"
  1. solar → local load (cheapest, one port)
  2. surplus → local battery charge
  3. deficit → local battery discharge
  4. remaining surplus/deficit → flows naturally through pandapower cables to
     peer nodes and ultimately to/from the grid via the mothership VSC

Policy 2 — Predictive look-ahead
  Same as greedy but uses perfect-foresight window to pre-charge batteries
  before forecast deficits (and pre-discharge before forecast large surpluses
  to make room) — showing the battery-cycling / self-sufficiency tradeoff.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from .ports import P_MAX_W

P_MAX_BATT_KW: float = P_MAX_W["battery"] / 1000.0   # 5 kW


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class NodeState:
    """Snapshot of one node's inputs for one timestep."""
    node_id: str
    solar_kw: float
    load_kw: float
    soc_kwh: float
    soc_pct: float
    battery_headroom_kw: float
    battery_available_kw: float
    has_battery: bool
    has_grid_port: bool
    # EV fields (zero / False if no EV on this node)
    ev_soc_kwh: float = 0.0
    ev_soc_pct: float = 0.0
    ev_headroom_kw: float = 0.0
    ev_available_kw: float = 0.0
    ev_plugged_in: bool = False
    # Generator fields (zero / False if no generator on this node)
    generator_available_kw: float = 0.0
    has_generator: bool = False
    # Demand response: True if this node's load is deferrable (e.g. pump/irrigation)
    is_deferrable: bool = False


@dataclass
class NodeSetpoints:
    """What the dispatch policy decides for one node, one timestep.

    Dispatchable decisions:
      battery_kw    — home battery  (+charge / −discharge)
      ev_battery_kw — EV battery    (+charge / −discharge)
      generator_kw  — generator requested output (≥ 0; Router clamps to p_rated)
      defer_load    — if True, the load for this node is deferred this step
                      (Router receives load_kw=0 and accumulates deferred_kwh)
    solar_kw is non-dispatchable; recorded for bookkeeping only.
    """
    node_id: str
    solar_kw: float = 0.0
    battery_kw: float = 0.0       # home battery: + charge, − discharge
    ev_battery_kw: float = 0.0    # EV battery:   + charge, − discharge
    generator_kw: float = 0.0     # generator: requested output (kW ≥ 0)
    defer_load: bool = False       # if True, skip serving this node's load this step


@dataclass
class CommunitySetpoints:
    """All nodes' setpoints for one timestep."""
    nodes: dict[str, NodeSetpoints] = field(default_factory=dict)

    def __getitem__(self, node_id: str) -> NodeSetpoints:
        return self.nodes[node_id]


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class DispatchPolicy(ABC):

    @abstractmethod
    def dispatch(
        self,
        states: dict[str, NodeState],
        dt_h: float = 0.25,
    ) -> CommunitySetpoints:
        """Decide battery setpoints for all nodes.

        Parameters
        ----------
        states : dict[node_id, NodeState]
        dt_h : float

        Returns
        -------
        CommunitySetpoints
        """


# ---------------------------------------------------------------------------
# Policy 1 — Greedy local-first
# ---------------------------------------------------------------------------

class GreedyPolicy(DispatchPolicy):
    """Per-node greedy: surplus PV → battery charge; deficit → battery discharge.

    No cross-node coordination needed — inter-node flows emerge from pandapower.
    """

    def dispatch(
        self,
        states: dict[str, NodeState],
        dt_h: float = 0.25,
    ) -> CommunitySetpoints:
        cs = CommunitySetpoints()
        for nid, st in states.items():
            battery_kw = 0.0
            residual = st.solar_kw - st.load_kw

            if st.has_battery:
                if residual > 0:
                    # Surplus: charge battery up to headroom and port limit
                    battery_kw = min(residual, st.battery_headroom_kw, P_MAX_BATT_KW)
                else:
                    # Deficit: discharge battery up to available and port limit
                    discharge = min(-residual, st.battery_available_kw, P_MAX_BATT_KW)
                    battery_kw = -discharge

            cs.nodes[nid] = NodeSetpoints(
                node_id=nid,
                solar_kw=st.solar_kw,
                battery_kw=battery_kw,
            )
        return cs


# ---------------------------------------------------------------------------
# Policy 2 — Predictive look-ahead
# ---------------------------------------------------------------------------

class PredictivePolicy(DispatchPolicy):
    """Look-ahead rule-based policy.

    Uses a perfect-foresight window to:
    - Pre-charge: if a net local deficit is forecast soon AND surplus exists
      now AND battery has room → charge more aggressively than greedy.
    - Pre-discharge: if a large local surplus is forecast soon AND battery
      is nearly full → discharge now to make room, reducing future curtailment.

    Falls back to greedy when no actionable foresight exists.
    """

    def __init__(
        self,
        future_solar_kw: dict[str, list[float]] | None = None,
        future_load_kw: dict[str, list[float]] | None = None,
        lookahead_steps: int = 4,
        pre_charge_threshold_kwh: float = 0.3,
        pre_discharge_soc_trigger_pct: float = 85.0,
    ) -> None:
        self._greedy = GreedyPolicy()
        self.future_solar = future_solar_kw or {}
        self.future_load = future_load_kw or {}
        self.lookahead = lookahead_steps
        self.threshold = pre_charge_threshold_kwh
        self.predischarge_trigger = pre_discharge_soc_trigger_pct
        self.step_idx = 0

    def update_forecasts(
        self,
        future_solar_kw: dict[str, list[float]],
        future_load_kw: dict[str, list[float]],
    ) -> None:
        self.future_solar = future_solar_kw
        self.future_load  = future_load_kw

    def dispatch(
        self,
        states: dict[str, NodeState],
        dt_h: float = 0.25,
    ) -> CommunitySetpoints:
        cs = self._greedy.dispatch(states, dt_h)

        i = self.step_idx
        for nid, st in states.items():
            if not st.has_battery:
                continue
            if nid not in self.future_solar or nid not in self.future_load:
                continue

            window = slice(i + 1, i + 1 + self.lookahead)
            fut_solar = np.array(self.future_solar[nid][window])
            fut_load  = np.array(self.future_load[nid][window])
            if len(fut_solar) == 0:
                continue

            fut_net = fut_solar - fut_load
            forecast_deficit_kwh  = max(0.0, -fut_net.sum() * dt_h)
            forecast_surplus_kwh  = max(0.0,  fut_net.sum() * dt_h)

            # Pre-charge: big deficit coming and we have local surplus now
            if (
                forecast_deficit_kwh > self.threshold
                and st.solar_kw > st.load_kw
                and st.battery_headroom_kw > 0
            ):
                want = min(
                    st.solar_kw - st.load_kw,
                    st.battery_headroom_kw,
                    P_MAX_BATT_KW,
                    forecast_deficit_kwh / dt_h,
                )
                cs.nodes[nid].battery_kw = max(cs.nodes[nid].battery_kw, want)

            # Pre-discharge: big surplus coming AND battery almost full → make room
            if (
                forecast_surplus_kwh > self.threshold
                and st.soc_pct > self.predischarge_trigger
                and st.battery_available_kw > 0
            ):
                release = min(
                    st.battery_available_kw,
                    P_MAX_BATT_KW,
                    forecast_surplus_kwh / dt_h,
                )
                cs.nodes[nid].battery_kw = min(cs.nodes[nid].battery_kw, -release)

        self.step_idx += 1
        return cs


# ---------------------------------------------------------------------------
# Policy 3 — Community Greedy (S's battery as community night buffer)
# ---------------------------------------------------------------------------

class CommunityGreedyPolicy(DispatchPolicy):
    """Greedy local-first, with the shared farm (S) acting as community buffer.

    Layer 1 — same as GreedyPolicy:  each house handles its own solar → load
              → local battery independently.
    Layer 2 — community buffer:  after local dispatch, if any house still has
              an unmet deficit (battery depleted), the farm with the biggest
              battery discharges to cover it.  The energy flows through the
              DC network naturally (pandapower handles routing).

    This is the minimal fix that unlocks the farm's 50 kWh battery for the
    community.  No look-ahead required.
    """

    def __init__(self, farm_id: str = "S") -> None:
        self._greedy = GreedyPolicy()
        self.farm_id = farm_id

    def dispatch(
        self,
        states: dict[str, NodeState],
        dt_h: float = 0.25,
    ) -> CommunitySetpoints:
        cs = self._greedy.dispatch(states, dt_h)

        farm_st = states.get(self.farm_id)
        if farm_st is None or not farm_st.has_battery:
            return cs

        # How much deficit remains after each house's own battery discharge?
        # local_deficit = max(0, load − solar − battery_discharge_provided)
        # battery_kw < 0 when discharging, so deficit = load − solar + battery_kw
        community_deficit = 0.0
        for nid, st in states.items():
            if nid == self.farm_id or st.has_grid_port:
                continue
            ns = cs.nodes.get(nid)
            if ns is None:
                continue
            # battery_kw ≤ 0 when discharging; covers (−battery_kw) of load
            local_deficit = max(0.0, st.load_kw - st.solar_kw + ns.battery_kw)
            community_deficit += local_deficit

        # Farm discharges to cover community deficit
        if community_deficit > 1e-4 and farm_st.battery_available_kw > 1e-4:
            farm_discharge = min(
                community_deficit,
                farm_st.battery_available_kw,
                P_MAX_BATT_KW,
            )
            # Farm's own solar may also be charging; discharge takes priority
            current = cs.nodes[self.farm_id].battery_kw
            cs.nodes[self.farm_id].battery_kw = max(current, 0.0) - farm_discharge

        return cs


# ---------------------------------------------------------------------------
# Policy 4 — Community Predictive (community buffer + pre-dawn pre-discharge)
# ---------------------------------------------------------------------------

class CommunityPredictivePolicy(DispatchPolicy):
    """Community buffer (Policy 3) + time-aware pre-positioning of the farm battery.

    On top of the community buffer logic, adds a pre-dawn discharge window
    (configurable, default 04:00–07:00) in which the farm pre-empties its
    battery to create headroom before the solar day.

    Why pre-dawn, not at noon:
      Discharging at noon into a community that is already exporting at the
      5 kW grid cap just causes curtailment — the energy has nowhere to go.
      Discharging at 5 AM reduces SoC before sunrise so the farm can absorb
      more morning PV before hitting its 95% ceiling, which delays curtailment
      by 1–2 hours and converts ~15 kWh of curtailed solar into stored energy.
    """

    def __init__(
        self,
        farm_id: str = "S",
        dawn_start_h: float = 4.0,
        dawn_end_h: float = 7.0,
        dawn_discharge_kw: float = 2.5,
        dawn_soc_threshold_pct: float = 55.0,
    ) -> None:
        self._community = CommunityGreedyPolicy(farm_id=farm_id)
        self.farm_id = farm_id
        self.dawn_start = dawn_start_h
        self.dawn_end = dawn_end_h
        self.dawn_discharge_kw = dawn_discharge_kw
        self.dawn_soc_threshold = dawn_soc_threshold_pct
        self._step = 0

    def dispatch(
        self,
        states: dict[str, NodeState],
        dt_h: float = 0.25,
    ) -> CommunitySetpoints:
        cs = self._community.dispatch(states, dt_h)

        farm_st = states.get(self.farm_id)
        if farm_st is not None and farm_st.has_battery:
            current_h = (self._step * dt_h) % 24.0
            in_dawn_window = self.dawn_start <= current_h < self.dawn_end
            above_threshold = farm_st.soc_pct > self.dawn_soc_threshold
            solar_off = farm_st.solar_kw < 0.5   # pre-sunrise

            if in_dawn_window and above_threshold and solar_off:
                extra_discharge = min(
                    self.dawn_discharge_kw,
                    farm_st.battery_available_kw,
                    P_MAX_BATT_KW,
                )
                if extra_discharge > 1e-4:
                    current = cs.nodes[self.farm_id].battery_kw
                    # Only increase discharge if pre-dawn gives more than community deficit did
                    cs.nodes[self.farm_id].battery_kw = min(current, -extra_discharge)

        self._step += 1
        return cs


# ---------------------------------------------------------------------------
# Policy 5 — EV-Aware Community (T5 / V2G)
# ---------------------------------------------------------------------------

class EVAwareCommunityPolicy(DispatchPolicy):
    """Community greedy + V2G EV charging/discharging with departure constraints.

    Per timestep, after running the standard community-greedy dispatch:

    EV charging priority (plugged in, surplus exists):
      1. After the local home battery is scheduled, any remaining surplus
         is used to top up the EV — targeting 80% SoC by departure time.
      2. In the pre-departure window (depart_h − 2h to depart_h), charge
         the EV at full power to ensure departure SoC is met.

    V2G discharge (plugged in, community has deficit):
      If the community still has an unmet deficit after the farm battery
      has discharged (community-greedy layer 2), the EV is used as a
      secondary V2G buffer — but only if EV SoC > min_ev_soc_pct.

    Parking schedule:
      - EV is "away" from depart_h to return_h (typically 8 AM – 6 PM).
      - ev_battery_kw is forced to 0 during away hours.

    Parameters
    ----------
    farm_id : str
    depart_h : float
        Departure hour (0–24), default 8.0 (8 AM).
    return_h : float
        Return hour (0–24), default 18.0 (6 PM).
    depart_soc_pct : float
        Target EV SoC at departure, default 80%.
    min_ev_soc_pct : float
        Minimum EV SoC allowed for V2G discharge, default 20%.
    pre_depart_window_h : float
        Hours before departure to switch to hard-charge mode, default 2.0.
    dt_h : float
        Timestep (hours) — used to compute step index from current_h.
    """

    def __init__(
        self,
        farm_id: str = "S",
        depart_h: float = 8.0,
        return_h: float = 18.0,
        depart_soc_pct: float = 80.0,
        min_ev_soc_pct: float = 20.0,
        pre_depart_window_h: float = 2.0,
    ) -> None:
        self._community = CommunityGreedyPolicy(farm_id=farm_id)
        self.farm_id = farm_id
        self.depart_h = depart_h
        self.return_h = return_h
        self.depart_soc_pct = depart_soc_pct
        self.min_ev_soc_pct = min_ev_soc_pct
        self.pre_depart_window_h = pre_depart_window_h
        self._step = 0

    def dispatch(
        self,
        states: dict[str, NodeState],
        dt_h: float = 0.25,
    ) -> CommunitySetpoints:
        cs = self._community.dispatch(states, dt_h)

        current_h = (self._step * dt_h) % 24.0
        self._step += 1

        # EV away window: depart_h → return_h (handles overnight wrap if needed)
        def _ev_away(h: float) -> bool:
            if self.depart_h < self.return_h:
                return self.depart_h <= h < self.return_h
            else:   # overnight departure (e.g. depart 22:00, return 6:00)
                return h >= self.depart_h or h < self.return_h

        ev_away = _ev_away(current_h)

        # Pre-departure hard-charge window
        pre_depart_start = (self.depart_h - self.pre_depart_window_h) % 24.0
        def _in_pre_depart(h: float) -> bool:
            if pre_depart_start <= self.depart_h:
                return pre_depart_start <= h < self.depart_h
            else:
                return h >= pre_depart_start or h < self.depart_h

        in_pre_depart = _in_pre_depart(current_h)

        for nid, st in states.items():
            if not st.ev_plugged_in or ev_away:
                cs.nodes[nid].ev_battery_kw = 0.0
                continue

            ns = cs.nodes[nid]
            ev_kw = 0.0

            # Pre-departure window: hard charge to hit departure target
            if in_pre_depart and st.ev_soc_pct < self.depart_soc_pct:
                ev_kw = min(st.ev_headroom_kw, P_MAX_BATT_KW)

            else:
                # Normal window: use surplus to top up EV
                net_residual = st.solar_kw - st.load_kw - ns.battery_kw
                if net_residual > 1e-4 and st.ev_headroom_kw > 1e-4:
                    ev_kw = min(net_residual, st.ev_headroom_kw, P_MAX_BATT_KW)

                # V2G: discharge EV if community still has deficit and EV has headroom
                elif (net_residual < -1e-4
                      and st.ev_available_kw > 1e-4
                      and st.ev_soc_pct > self.min_ev_soc_pct):
                    ev_kw = -min(-net_residual, st.ev_available_kw, P_MAX_BATT_KW)

            ns.ev_battery_kw = ev_kw

        return cs


# ---------------------------------------------------------------------------
# Policy 6 — Generator-Aware Community (islanding with diesel/biogas backup)
# ---------------------------------------------------------------------------

class GeneratorAwareCommunityPolicy(DispatchPolicy):
    """Community greedy + automatic generator dispatch during islanding.

    When a node has a generator configured (`has_generator=True`) and the
    community cannot meet load from solar + batteries alone, the generator
    is dispatched at enough power to close the gap — up to its rated output.

    This is particularly useful for:
      - Telecom towers with diesel gensets
      - Health centres requiring 24/7 supply
      - Cold storage needing continuous compressor power

    Parameters
    ----------
    farm_id : str
        Node with the community battery buffer.
    gen_node_ids : list[str] | None
        Which nodes have generators to dispatch.  None = dispatch on all nodes
        that have `has_generator=True`.
    gen_threshold_kw : float
        Minimum community deficit (kW) before generator is started.
        Avoids light-loading at <25% rated power (poor SFC).
    """

    def __init__(
        self,
        farm_id: str = "S",
        gen_node_ids: list[str] | None = None,
        gen_threshold_kw: float = 1.0,
    ) -> None:
        self._community = CommunityGreedyPolicy(farm_id=farm_id)
        self.farm_id = farm_id
        self.gen_node_ids = gen_node_ids
        self.gen_threshold_kw = gen_threshold_kw

    def dispatch(
        self,
        states: dict[str, NodeState],
        dt_h: float = 0.25,
    ) -> CommunitySetpoints:
        cs = self._community.dispatch(states, dt_h)

        # Estimate community deficit after battery dispatch
        community_deficit = 0.0
        for nid, st in states.items():
            ns = cs.nodes.get(nid)
            if ns is None:
                continue
            local_deficit = max(0.0, st.load_kw - st.solar_kw + ns.battery_kw)
            community_deficit += local_deficit

        if community_deficit < self.gen_threshold_kw:
            return cs

        remaining_deficit = community_deficit
        for nid, st in states.items():
            if not st.has_generator:
                continue
            if self.gen_node_ids is not None and nid not in self.gen_node_ids:
                continue
            if remaining_deficit < 1e-4:
                break
            gen_kw = min(remaining_deficit, st.generator_available_kw)
            cs.nodes[nid].generator_kw = gen_kw
            remaining_deficit -= gen_kw

        return cs


# ---------------------------------------------------------------------------
# Policy 7 — Demand Response (pump/irrigation load deferral)
# ---------------------------------------------------------------------------

class DemandResponsePolicy(DispatchPolicy):
    """Community greedy + solar-timed deferral of flexible (pump) loads.

    Pump nodes (is_deferrable=True) are scheduled to run only when there is
    a community solar surplus.  If no surplus exists at the pump's scheduled
    time, the load is deferred and the deficit is counted as 'deferred_kwh'.

    This models a DISCOM or microgrid-operator demand response programme where
    irrigation pump schedules are shifted to coincide with peak PV generation
    (reducing grid import and curtailment simultaneously).

    Parameters
    ----------
    farm_id : str
    surplus_threshold_kw : float
        Minimum community net surplus (solar - load) before a deferred pump
        load is allowed to run.  Default 0.5 kW prevents marginal dispatch.
    """

    def __init__(
        self,
        farm_id: str = "S",
        surplus_threshold_kw: float = 0.5,
    ) -> None:
        self._community = CommunityGreedyPolicy(farm_id=farm_id)
        self.farm_id = farm_id
        self.surplus_threshold_kw = surplus_threshold_kw

    def dispatch(
        self,
        states: dict[str, NodeState],
        dt_h: float = 0.25,
    ) -> CommunitySetpoints:
        cs = self._community.dispatch(states, dt_h)

        # Community-wide surplus after battery dispatch
        community_surplus = 0.0
        for nid, st in states.items():
            ns = cs.nodes.get(nid)
            if ns is None:
                continue
            # net bus = solar − load − battery_charge + battery_discharge
            community_surplus += st.solar_kw - st.load_kw - ns.battery_kw

        for nid, st in states.items():
            if not st.is_deferrable:
                continue
            # Defer the pump if community is in deficit (not enough sun)
            if community_surplus < self.surplus_threshold_kw:
                cs.nodes[nid].defer_load = True

        return cs


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_policy(name: str = "greedy", **kwargs) -> DispatchPolicy:
    """Create a policy by name.

    Names: 'greedy' | 'predictive' | 'community' | 'community_predictive' |
           'ev_aware' | 'generator_aware' | 'demand_response'
    """
    if name == "greedy":
        return GreedyPolicy()
    if name == "predictive":
        return PredictivePolicy(**kwargs)
    if name == "community":
        return CommunityGreedyPolicy(**{k: v for k, v in kwargs.items()
                                        if k in ("farm_id",)})
    if name == "community_predictive":
        return CommunityPredictivePolicy(**{k: v for k, v in kwargs.items()
                                            if k in ("farm_id", "dawn_start_h", "dawn_end_h",
                                                     "dawn_discharge_kw", "dawn_soc_threshold_pct")})
    if name == "ev_aware":
        return EVAwareCommunityPolicy(**{k: v for k, v in kwargs.items()
                                         if k in ("farm_id", "depart_h", "return_h",
                                                   "depart_soc_pct", "min_ev_soc_pct",
                                                   "pre_depart_window_h")})
    if name == "generator_aware":
        return GeneratorAwareCommunityPolicy(**{k: v for k, v in kwargs.items()
                                                if k in ("farm_id", "gen_node_ids",
                                                         "gen_threshold_kw")})
    if name == "demand_response":
        return DemandResponsePolicy(**{k: v for k, v in kwargs.items()
                                       if k in ("farm_id", "surplus_threshold_kw")})
    raise ValueError(f"Unknown policy '{name}'")
