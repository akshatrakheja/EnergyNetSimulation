"""
metrics.py — Compute all §10 metrics from a SimResults object.

Metrics returned for every run:
  - self_sufficiency_pct      : 1 − (grid_import ÷ total_load) × 100
  - self_consumption_pct      : locally_used_pv ÷ total_pv × 100
  - peak_grid_draw_kw         : max single-step grid import
  - peak_shave_pct            : reduction vs uncoordinated baseline (if provided)
  - total_port_loss_kwh       : Σ converter losses across all ports
  - total_cable_loss_kwh      : Σ I²R losses from pandapower
  - total_standby_loss_kwh    : not broken out separately (folded into port losses)
  - total_loss_kwh            : port + cable
  - peer_shared_kwh           : energy that flowed house↔house without going through M
  - grid_import_kwh           : total grid energy consumed
  - grid_export_kwh           : total grid energy pushed back
  - curtailed_kwh             : PV generation that was curtailed
  - voltage_band_compliance   : fraction of (bus, step) within ±5% of nominal
  - soc_trajectories          : {node_id: Series(t → soc_pct)}
  - battery_cycles            : {node_id: float}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .simulate import SimResults


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VM_BAND_PCT: float = 5.0     # ±5% voltage band (configurable)
VM_NOMINAL_PU: float = 1.0


# ---------------------------------------------------------------------------
# Metrics container
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    self_sufficiency_pct: float = float("nan")
    self_consumption_pct: float = float("nan")
    peak_grid_draw_kw: float = float("nan")
    peak_shave_pct: float = float("nan")

    total_load_kwh: float = float("nan")
    total_solar_kwh: float = float("nan")
    total_port_loss_kwh: float = float("nan")
    total_cable_loss_kwh: float = float("nan")
    total_loss_kwh: float = float("nan")

    grid_import_kwh: float = float("nan")
    grid_export_kwh: float = float("nan")
    curtailed_kwh: float = float("nan")
    shed_load_kwh: float = 0.0          # unserved demand during grid outage
    shed_critical_kwh: float = 0.0      # subset of shed_load that was critical
    deferred_kwh: float = 0.0           # load deferred by demand-response (pump/irrigation)
    generator_fuel_L: float = 0.0       # total diesel/fuel consumed across all generators
    peer_shared_kwh: float = float("nan")

    voltage_band_compliance_pct: float = float("nan")
    voltage_violations: list[dict] = field(default_factory=list)

    soc_trajectories: dict[str, pd.Series] = field(default_factory=dict)
    battery_cycles: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        """One-paragraph text summary."""
        lines = [
            "=== EnergyNet Phase 2 — Run Metrics ===",
            f"  Self-sufficiency      : {self.self_sufficiency_pct:.1f} %",
            f"  Self-consumption      : {self.self_consumption_pct:.1f} %",
            f"  Peak grid draw        : {self.peak_grid_draw_kw:.2f} kW",
            f"  Peak-shave            : {self.peak_shave_pct:.1f} % (vs uncoordinated)",
            f"  Grid import           : {self.grid_import_kwh:.3f} kWh",
            f"  Grid export           : {self.grid_export_kwh:.3f} kWh",
        f"  Curtailed PV          : {self.curtailed_kwh:.3f} kWh",
        f"  Shed load (outage)    : {self.shed_load_kwh:.3f} kWh"
            + (f"  (critical: {self.shed_critical_kwh:.1f} kWh)" if self.shed_critical_kwh > 0 else ""),
        f"  Deferred load (DR)    : {self.deferred_kwh:.3f} kWh",
        f"  Generator fuel        : {self.generator_fuel_L:.1f} L",
        f"  Peer-shared energy    : {self.peer_shared_kwh:.3f} kWh",
            f"  Port-conversion loss  : {self.total_port_loss_kwh:.3f} kWh",
            f"  Cable loss            : {self.total_cable_loss_kwh:.3f} kWh",
            f"  Total losses          : {self.total_loss_kwh:.3f} kWh",
            f"  Voltage compliance    : {self.voltage_band_compliance_pct:.1f} % (±{VM_BAND_PCT}%)",
        ]
        for nid, cyc in self.battery_cycles.items():
            lines.append(f"  Battery cycles {nid:4s}   : {cyc:.2f}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "self_sufficiency_pct": self.self_sufficiency_pct,
            "self_consumption_pct": self.self_consumption_pct,
            "peak_grid_draw_kw": self.peak_grid_draw_kw,
            "peak_shave_pct": self.peak_shave_pct,
            "total_load_kwh": self.total_load_kwh,
            "total_solar_kwh": self.total_solar_kwh,
            "total_port_loss_kwh": self.total_port_loss_kwh,
            "total_cable_loss_kwh": self.total_cable_loss_kwh,
            "total_loss_kwh": self.total_loss_kwh,
            "grid_import_kwh": self.grid_import_kwh,
            "grid_export_kwh": self.grid_export_kwh,
            "curtailed_kwh": self.curtailed_kwh,
            "shed_load_kwh": self.shed_load_kwh,
            "shed_critical_kwh": self.shed_critical_kwh,
            "deferred_kwh": self.deferred_kwh,
            "generator_fuel_L": self.generator_fuel_L,
            "peer_shared_kwh": self.peer_shared_kwh,
            "voltage_band_compliance_pct": self.voltage_band_compliance_pct,
            "battery_cycles": self.battery_cycles,
        }


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

def compute_metrics(
    results: SimResults,
    baseline_peak_kw: float | None = None,
) -> Metrics:
    """Compute all §10 metrics from a completed SimResults.

    Parameters
    ----------
    results : SimResults
    baseline_peak_kw : float | None
        Uncoordinated "each house alone" peak grid draw (for peak-shave %).
        Provide from a separate baseline run or omit.
    """
    m = Metrics()
    df = results.to_dataframe()
    dt_h = results.dt_h
    node_ids = list(results.node_cfg.keys())

    # ---- Load & Solar totals ----
    load_cols   = [f"{n}_load_kw"            for n in node_ids if f"{n}_load_kw"            in df.columns]
    solar_cols  = [f"{n}_solar_backplane_kw" for n in node_ids if f"{n}_solar_backplane_kw" in df.columns]
    solar_loss_cols = [f"{n}_solar_loss_kw"  for n in node_ids if f"{n}_solar_loss_kw"      in df.columns]

    m.total_load_kwh  = float(df[load_cols].sum().sum() * dt_h)
    # Total solar available = backplane delivery + solar port conversion losses
    solar_backplane_kwh = float(df[solar_cols].sum().sum() * dt_h)
    solar_loss_kwh      = float(df[solar_loss_cols].sum().sum() * dt_h)
    m.total_solar_kwh  = solar_backplane_kwh + solar_loss_kwh

    # Curtailment is at community level (from simulate.py StepRecord)
    m.curtailed_kwh    = float(df["curtailed_kw"].sum() * dt_h) if "curtailed_kw" in df.columns else 0.0

    # ---- Grid import / export ----
    grid_kw = df["grid_exchange_kw"]
    grid_import_kw  = grid_kw.clip(lower=0)
    grid_export_kw  = (-grid_kw).clip(lower=0)
    m.grid_import_kwh = float(grid_import_kw.sum() * dt_h)
    m.grid_export_kwh = float(grid_export_kw.sum() * dt_h)

    # ---- Self-sufficiency ----
    if m.total_load_kwh > 0:
        m.self_sufficiency_pct = 100.0 * (1.0 - m.grid_import_kwh / m.total_load_kwh)
    else:
        m.self_sufficiency_pct = 100.0

    # ---- Self-consumption (IEC definition) ----
    # SC = solar consumed within the community / total solar generated
    # "Consumed within community" excludes both curtailed and grid-exported solar.
    # Grid export is solar that left the community, so it does NOT count as self-consumed.
    locally_used_solar_kwh = m.total_solar_kwh - m.curtailed_kwh - m.grid_export_kwh
    locally_used_solar_kwh = max(0.0, locally_used_solar_kwh)
    if m.total_solar_kwh > 0:
        m.self_consumption_pct = 100.0 * locally_used_solar_kwh / m.total_solar_kwh
    else:
        m.self_consumption_pct = float("nan")

    # ---- Shed load (grid outage) ----
    m.shed_load_kwh      = float(df["shed_load_kw"].sum()      * dt_h) if "shed_load_kw"      in df.columns else 0.0
    m.shed_critical_kwh  = float(df["shed_critical_kw"].sum()  * dt_h) if "shed_critical_kw"  in df.columns else 0.0
    m.deferred_kwh       = float(df["deferred_kw"].sum()       * dt_h) if "deferred_kw"       in df.columns else 0.0

    # ---- Generator fuel consumption ----
    gen_fuel_cols = [c for c in df.columns if c.endswith("_generator_fuel_L")]
    m.generator_fuel_L = float(df[gen_fuel_cols].sum().sum()) if gen_fuel_cols else 0.0

    # ---- Peak grid draw & peak-shave ----
    m.peak_grid_draw_kw = float(grid_import_kw.max())
    if baseline_peak_kw and baseline_peak_kw > 0:
        m.peak_shave_pct = 100.0 * (1.0 - m.peak_grid_draw_kw / baseline_peak_kw)
    else:
        # Estimate uncoordinated baseline: max(total_load − total_solar, 0) per step.
        # This is what the community would draw from the grid at each step without
        # any battery buffering — i.e. every load-solar gap is instantly grid-served.
        load_series  = df[load_cols].sum(axis=1)  if load_cols  else pd.Series(0.0, index=df.index)
        solar_series = df[solar_cols].sum(axis=1) if solar_cols else pd.Series(0.0, index=df.index)
        uncoordinated_peak = float((load_series - solar_series).clip(lower=0).max())
        if uncoordinated_peak > 0:
            m.peak_shave_pct = 100.0 * (1.0 - m.peak_grid_draw_kw / uncoordinated_peak)
        else:
            m.peak_shave_pct = float("nan")

    # ---- Losses ----
    port_loss_cols = []
    for n in node_ids:
        for kind in ["solar_loss_kw", "batt_loss_kw", "standby_loss_kw"]:
            col = f"{n}_{kind}"
            if col in df.columns:
                port_loss_cols.append(col)

    node_port_loss_kwh = float(df[port_loss_cols].sum().sum() * dt_h)
    peer_port_loss_kwh = float(df["peer_port_loss_kw"].sum() * dt_h) if "peer_port_loss_kw" in df.columns else 0.0
    grid_port_loss_kwh = float(df["grid_port_loss_kw"].sum() * dt_h) if "grid_port_loss_kw" in df.columns else 0.0

    m.total_port_loss_kwh  = node_port_loss_kwh + peer_port_loss_kwh + grid_port_loss_kwh
    m.total_cable_loss_kwh = float(df["cable_loss_kw"].sum() * dt_h)
    m.total_loss_kwh       = m.total_port_loss_kwh + m.total_cable_loss_kwh

    # ---- Peer-shared energy ----
    # Only the H1-H2 and H2-H3 cables are direct house-to-house links that
    # bypass the mothership M. Energy flowing through those cables is "peer-shared".
    # We pull the per-step cable flows stored in StepRecord.line_flows_mw.
    peer_cable_names = {"H1-H2", "H2-H3"}
    peer_flow_kwh = 0.0
    for step in results.steps:
        for cable, flow_mw in step.line_flows_mw.items():
            if cable in peer_cable_names:
                peer_flow_kwh += abs(float(flow_mw)) * 1000.0 * dt_h
    m.peer_shared_kwh = peer_flow_kwh

    # ---- Voltage band compliance ----
    vm_cols = [f"{n}_vm_pu" for n in node_ids if f"{n}_vm_pu" in df.columns]
    if vm_cols:
        vm_arr = df[vm_cols].values.flatten()
        vm_arr = vm_arr[~np.isnan(vm_arr)]
        lo = VM_NOMINAL_PU * (1 - VM_BAND_PCT / 100)
        hi = VM_NOMINAL_PU * (1 + VM_BAND_PCT / 100)
        in_band = np.sum((vm_arr >= lo) & (vm_arr <= hi))
        m.voltage_band_compliance_pct = 100.0 * in_band / len(vm_arr) if len(vm_arr) > 0 else float("nan")

        # Log violations
        for col in vm_cols:
            nid = col.replace("_vm_pu", "")
            viol_mask = (df[col] < lo) | (df[col] > hi)
            for ts, vm_val in df.loc[viol_mask, col].items():
                m.voltage_violations.append({"node": nid, "t": ts, "vm_pu": vm_val})

    # ---- SoC trajectories ----
    for n in node_ids:
        col = f"{n}_soc_pct"
        if col in df.columns:
            m.soc_trajectories[n] = df[col].copy()

    # ---- Battery cycles (cumulative throughput method) ----
    for n in node_ids:
        col = f"{n}_soc_pct"
        cap_kwh = results.node_cfg[n].get("battery_kwh", 0.0)
        if col in df.columns and cap_kwh > 0:
            soc_pct = df[col].values
            m.battery_cycles[n] = _approximate_cycles(soc_pct, cap_kwh)

    return m


def _approximate_cycles(soc_pct: np.ndarray, capacity_kwh: float) -> float:
    """Approximate full-cycle count from SoC trajectory.

    Uses cumulative throughput method: cycles = Σ|ΔSOC| / (2 × 100%).
    """
    delta = np.abs(np.diff(soc_pct))
    return float(delta.sum() / 200.0)
