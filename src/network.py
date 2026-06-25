"""
network.py — Build EnergyNet EWAN in pandapower, driven by TopologyConfig.

Topologies implemented:
  T1_STAR     — baseline star (M hub, peer H1-H2, H2-H3 shortcuts)
  T4_RING     — star + H3→H1 closure (ring main, better resilience)
  T_S_SPOKE   — star + S wired directly to each house (farm as peer hub)
  T_DC_BUS    — ring main with no central hub (M is just one tap)
  T6_APARTMENT— short-run star for multi-storey building (S4 variant)

All topologies share the same update / result interface (set_bus_powers /
run_powerflow / extract_results) so simulate.py does not need topology
awareness beyond passing the right TopologyConfig to build_network.

Sign convention:
  net_bus_power_kw > 0  → node injects into the network
  net_bus_power_kw < 0  → node draws from the network
  grid_exchange_kw  > 0 → grid import (VSC/ext_grid feeds the DC net)
  grid_exchange_kw  < 0 → grid export (DC net pushes to AC grid)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandapower as pp
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NODE_IDS: list[str] = ["M", "S", "H1", "H2", "H3"]
LOAD_NODES: list[str] = ["S", "H1", "H2", "H3"]

DEFAULT_CABLE_R_OHM_PER_KM: float = 0.5      # ≈ 25 mm² copper
DEFAULT_CABLE_MAX_I_KA: float = 0.05          # 50 A → ~20 kW @ 400 V
VN_DC_KV: float = 0.4
VN_AC_KV: float = 10.0


# ---------------------------------------------------------------------------
# Topology configuration
# ---------------------------------------------------------------------------

@dataclass
class TopologyConfig:
    """Data-driven cable layout for the DC microgrid.

    Parameters
    ----------
    name : str
        Human-readable label used in output filenames.
    cables : list[tuple[str, str, float]]
        List of (from_node, to_node, length_km) tuples for the base
        5-node network (M, S, H1, H2, H3).  Extra house nodes (S4/T6
        scaling) are always added as star spokes from M.
    slack_node : str
        Node that holds the VSC (DC voltage reference = grid slack).
        Must be in NODE_IDS.  Default: 'M'.
    extra_house_cable_length_km : float
        Cable length used for auto-generated spokes to extra house nodes.
        0.060 km (60 m) for community star; 0.010 km (10 m) for apartment.
    description : str
        One-line plain-English description shown in reports.
    """
    name: str
    cables: list[tuple[str, str, float]]
    slack_node: str = "M"
    extra_house_cable_length_km: float = 0.060
    description: str = ""


# ---------------------------------------------------------------------------
# Predefined topology catalogue
# ---------------------------------------------------------------------------

T1_STAR = TopologyConfig(
    name="T1_star",
    cables=[
        ("M",  "S",  0.040),
        ("M",  "H1", 0.030),
        ("M",  "H2", 0.060),
        ("M",  "H3", 0.090),
        ("H1", "H2", 0.035),
        ("H2", "H3", 0.045),
    ],
    description="Baseline star: M hub, H1-H2 and H2-H3 peer shortcuts.",
)

T4_RING = TopologyConfig(
    name="T4_ring",
    cables=[
        ("M",  "S",  0.040),
        ("M",  "H1", 0.030),
        ("M",  "H2", 0.060),
        ("M",  "H3", 0.090),
        ("H1", "H2", 0.035),
        ("H2", "H3", 0.045),
        ("H3", "H1", 0.070),   # ring closure — H1 reachable from H3 without M
    ],
    description="Ring: adds H3→H1 closure for resilience + alternate routing.",
)

T_S_SPOKE = TopologyConfig(
    name="T_S_spoke",
    cables=[
        ("M",  "S",  0.040),
        ("M",  "H1", 0.030),
        ("M",  "H2", 0.060),
        ("M",  "H3", 0.090),
        ("H1", "H2", 0.035),
        ("H2", "H3", 0.045),
        # Farm S directly wired to each house — farm battery reaches houses
        # in one hop instead of two (S→M→Hx).
        ("S",  "H1", 0.050),
        ("S",  "H2", 0.080),
        ("S",  "H3", 0.110),
    ],
    description="S-spoke: farm wired directly to each house, bypassing M for farm-to-house flows.",
)

T_DC_BUS = TopologyConfig(
    name="T_DC_bus",
    cables=[
        # Single ring-main backbone: M-S-H1-H2-H3-M
        # All nodes are peers on the bus; no central hub.
        # M is just one tap on the ring (happens to hold the VSC).
        ("M",  "S",  0.040),
        ("S",  "H1", 0.060),
        ("H1", "H2", 0.035),
        ("H2", "H3", 0.045),
        ("H3", "M",  0.090),
    ],
    description="DC ring main: no hub, all nodes peers on a shared backbone.",
)

T6_APARTMENT = TopologyConfig(
    name="T6_apartment",
    cables=[
        # Farm on same rooftop as M — very short run.
        ("M",  "S",  0.010),
        # H1/H2/H3 are replaced by 21 apartment nodes, all auto-generated
        # as 10 m star spokes from M (within the same building).
    ],
    extra_house_cable_length_km=0.010,
    description="Apartment block: shared rooftop farm, ultra-short internal runs, no peer cables.",
)

TOPOLOGY_CATALOGUE: dict[str, TopologyConfig] = {
    t.name: t for t in [T1_STAR, T4_RING, T_S_SPOKE, T_DC_BUS, T6_APARTMENT]
}


# ---------------------------------------------------------------------------
# Network container
# ---------------------------------------------------------------------------

@dataclass
class EnergyNet:
    """Wraps a pandapowerNet plus index bookkeeping."""
    net: pp.pandapowerNet
    approach: Literal["dc", "ac"]
    topology: TopologyConfig
    bus_index: dict[str, int] = field(default_factory=dict)
    load_index: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_network(
    topology_cfg: TopologyConfig | None = None,
    approach: Literal["dc", "ac"] = "dc",
    cable_r_ohm_per_km: float = DEFAULT_CABLE_R_OHM_PER_KM,
    cable_max_i_ka: float = DEFAULT_CABLE_MAX_I_KA,
    n_extra_houses: int = 0,
) -> EnergyNet:
    """Build the EWAN pandapower model for a given topology.

    Parameters
    ----------
    topology_cfg : TopologyConfig | None
        Cable layout.  Defaults to T1_STAR (baseline).
    approach : 'dc' | 'ac'
        'dc' uses native DC elements (pandapower ≥ 3.0, preferred).
        'ac' uses a resistive AC equivalent.
    cable_r_ohm_per_km : float
        Resistance per km (0.5 Ω/km ≈ 25 mm² copper).
    cable_max_i_ka : float
        Ampacity limit.
    n_extra_houses : int
        Extra house nodes beyond H1/H2/H3 (S4 / T6 scaling).
        They are always connected to the slack node (M) via star spokes.
    """
    cfg = topology_cfg or T1_STAR
    if approach == "dc":
        return _build_dc(cfg, cable_r_ohm_per_km, cable_max_i_ka, n_extra_houses)
    else:
        return _build_ac(cfg, cable_r_ohm_per_km, cable_max_i_ka, n_extra_houses)


def _all_nodes(cfg: TopologyConfig, n_extra: int) -> list[str]:
    """Return all node IDs: base nodes + extra house nodes."""
    base = NODE_IDS[:]
    extra = [f"H{4 + i}" for i in range(n_extra)]
    return base + extra


def _build_dc(
    cfg: TopologyConfig,
    r: float,
    max_i_ka: float,
    n_extra: int,
) -> EnergyNet:
    net = pp.create_empty_network()
    en = EnergyNet(net=net, approach="dc", topology=cfg)

    # AC coupling bus + external grid
    ac_bus = pp.create_bus(net, vn_kv=VN_AC_KV, name="AC_slack")
    pp.create_ext_grid(net, bus=ac_bus, vm_pu=1.0)

    # DC buses — base nodes first, then extra houses
    for nid in _all_nodes(cfg, n_extra):
        idx = pp.create_bus_dc(net, vn_kv=VN_DC_KV, name=nid)
        en.bus_index[nid] = idx

    # VSC: AC slack ↔ DC slack node (M by default)
    pp.create_vsc(
        net,
        bus=ac_bus,
        bus_dc=en.bus_index[cfg.slack_node],
        r_ohm=0.01,
        x_ohm=0.1,
        r_dc_ohm=0.001,
        control_mode_ac="vm_pu",
        control_value_ac=1.0,
        control_mode_dc="vm_pu",
        control_value_dc=1.0,
    )

    # Base topology cables (from TopologyConfig)
    for (a, b, length_km) in cfg.cables:
        if a not in en.bus_index or b not in en.bus_index:
            continue   # skip if node not in this network (e.g. T6 skips H1/H2/H3)
        pp.create_line_dc_from_parameters(
            net,
            from_bus_dc=en.bus_index[a],
            to_bus_dc=en.bus_index[b],
            length_km=length_km,
            r_ohm_per_km=r,
            max_i_ka=max_i_ka,
            name=f"{a}-{b}",
        )

    # Extra house cables (always star spokes to slack node)
    for i in range(n_extra):
        nid = f"H{4 + i}"
        pp.create_line_dc_from_parameters(
            net,
            from_bus_dc=en.bus_index[cfg.slack_node],
            to_bus_dc=en.bus_index[nid],
            length_km=cfg.extra_house_cable_length_km,
            r_ohm_per_km=r,
            max_i_ka=max_i_ka,
            name=f"{cfg.slack_node}-{nid}",
        )

    # load_dc on every non-slack node (explicit index= for pandapower 3.4 bug)
    all_load_nodes = [n for n in _all_nodes(cfg, n_extra) if n != cfg.slack_node]
    for seq_i, nid in enumerate(all_load_nodes):
        pp.create_load_dc(
            net, bus_dc=en.bus_index[nid], p_dc_mw=0.0,
            name=f"{nid}_net", index=seq_i,
        )
        en.load_index[nid] = seq_i

    return en


def _build_ac(
    cfg: TopologyConfig,
    r: float,
    max_i_ka: float,
    n_extra: int,
) -> EnergyNet:
    """Approach B: DC network modelled as AC with near-zero reactance."""
    net = pp.create_empty_network()
    en = EnergyNet(net=net, approach="ac", topology=cfg)

    for nid in _all_nodes(cfg, n_extra):
        idx = pp.create_bus(net, vn_kv=VN_DC_KV, name=nid)
        en.bus_index[nid] = idx

    pp.create_ext_grid(net, bus=en.bus_index[cfg.slack_node], vm_pu=1.0)

    for (a, b, length_km) in cfg.cables:
        if a not in en.bus_index or b not in en.bus_index:
            continue
        pp.create_line_from_parameters(
            net,
            from_bus=en.bus_index[a],
            to_bus=en.bus_index[b],
            length_km=length_km,
            r_ohm_per_km=r,
            x_ohm_per_km=1e-6,
            c_nf_per_km=0.0,
            max_i_ka=max_i_ka,
            name=f"{a}-{b}",
        )

    for i in range(n_extra):
        nid = f"H{4 + i}"
        pp.create_line_from_parameters(
            net,
            from_bus=en.bus_index[cfg.slack_node],
            to_bus=en.bus_index[nid],
            length_km=cfg.extra_house_cable_length_km,
            r_ohm_per_km=r,
            x_ohm_per_km=1e-6,
            c_nf_per_km=0.0,
            max_i_ka=max_i_ka,
            name=f"{cfg.slack_node}-{nid}",
        )

    all_load_nodes = [n for n in _all_nodes(cfg, n_extra) if n != cfg.slack_node]
    for seq_i, nid in enumerate(all_load_nodes):
        pp.create_load(
            net, bus=en.bus_index[nid], p_mw=0.0, q_mvar=0.0,
            name=f"{nid}_net", index=seq_i,
        )
        en.load_index[nid] = seq_i

    return en


# ---------------------------------------------------------------------------
# Update and run
# ---------------------------------------------------------------------------

def set_bus_powers(en: EnergyNet, net_kw: dict[str, float]) -> None:
    """Write per-node net powers into the pandapower model (before runpp).

    Positive net_kw → node injects (source).
    Negative net_kw → node draws (load).
    Slack node and unknown nodes are silently skipped.
    """
    for nid, p_kw in net_kw.items():
        if nid not in en.load_index:
            continue
        p_mw = -p_kw / 1000.0   # load_dc convention: positive = consumption
        if en.approach == "dc":
            en.net.load_dc.at[en.load_index[nid], "p_dc_mw"] = p_mw
        else:
            en.net.load.at[en.load_index[nid], "p_mw"] = p_mw


def run_powerflow(en: EnergyNet) -> bool:
    """Run AC (hybrid) power flow. Returns True if converged."""
    try:
        pp.runpp(en.net, verbose=False, numba=False)
        return bool(en.net.converged)
    except Exception:
        return False


def extract_results(en: EnergyNet) -> dict:
    """Pull results from the solved network.

    Returns dict with keys:
        converged        : bool
        vm_pu            : dict[node_id, float]
        cable_loss_kw    : float
        grid_exchange_kw : float  (+ import, − export)
        line_flows_mw    : dict[cable_name, float]
        line_loading_pct : dict[cable_name, float]
    """
    net = en.net
    converged = bool(net.converged)
    result: dict = {"converged": converged}

    if not converged:
        result.update({
            "vm_pu": {}, "cable_loss_kw": float("nan"),
            "grid_exchange_kw": float("nan"),
            "line_flows_mw": {}, "line_loading_pct": {},
        })
        return result

    # Bus voltages
    if en.approach == "dc":
        vm = net.res_bus_dc["vm_pu"]
    else:
        vm = net.res_bus["vm_pu"]
    result["vm_pu"] = {nid: float(vm.iloc[idx]) for nid, idx in en.bus_index.items()}

    # Cable losses (p_from + p_to = I²R loss)
    if en.approach == "dc":
        res_l = net.res_line_dc
        loss_mw = (res_l["p_from_mw"] + res_l["p_to_mw"]).sum()
        result["cable_loss_kw"] = float(max(0.0, loss_mw) * 1000.0)
        result["line_flows_mw"] = {
            str(name): float(pf) for name, pf in
            zip(net.line_dc["name"], res_l["p_from_mw"])
        }
        result["line_loading_pct"] = {
            str(name): float(v) for name, v in
            zip(net.line_dc["name"], res_l["loading_percent"])
        }
    else:
        res_l = net.res_line
        loss_mw = res_l["pl_mw"].sum()
        result["cable_loss_kw"] = float(max(0.0, loss_mw) * 1000.0)
        result["line_flows_mw"] = {
            str(name): float(pf) for name, pf in
            zip(net.line["name"], res_l["p_from_mw"])
        }
        result["line_loading_pct"] = {
            str(name): float(v) for name, v in
            zip(net.line["name"], res_l["loading_percent"])
        }

    # Grid exchange via VSC (DC) or ext_grid (AC)
    # VSC p_dc_mw is in load convention: + = absorbing from DC (export),
    #   − = injecting into DC (import).  Flip sign: + = import, − = export.
    if en.approach == "dc":
        p_dc = float(net.res_vsc["p_dc_mw"].iloc[0])
        result["grid_exchange_kw"] = -p_dc * 1000.0
    else:
        p_ext = float(net.res_ext_grid["p_mw"].iloc[0])
        result["grid_exchange_kw"] = p_ext * 1000.0

    return result
