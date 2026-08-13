"""
FastAPI backend for EnergyNet Simulator.

Two-tier architecture:
  - Physical config change → full simulation (slow, ~15s for 1-day preview)
  - Economic slider change → recompute economics only (fast, <50ms)

SimResults are cached by physical config hash so economic recalculations
don't re-trigger the physics engine.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import sys
from pathlib import Path

# Add project root so we can import src.*
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from src.simulate import run_simulation, SimResults
from src.profiles import load_profiles, pv_profiles, generate_weather_sequence
from src.metrics import compute_metrics, Metrics
from src.economics import compute_economics, EconResult, CapexConfig, FinanceConfig
from src.markets import MarketConfig
from src.subsidies import SubsidyStack
from src.dispatch import (
    OutagePreChargePolicy,
    OutageOnlyGensetPolicy,
    GeneratorAwareCommunityPolicy,
)

app = FastAPI(title="EnergyNet Simulator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_sim_cache: dict[str, dict] = {}  # hash → {results, metrics, market, timestamp}
MAX_CACHE = 20


def _config_hash(nodes: list[dict], env: dict) -> str:
    blob = json.dumps({"nodes": nodes, "env": env}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class NodeSpec(BaseModel):
    id: str
    node_type: str = "residential"
    pv_kw: float = 0.0
    battery_kwh: float = 0.0
    avg_load_kw: float = 1.5
    has_grid_port: bool = False
    generator_kw: float = 0.0
    gen_fuel_type: str = "diesel"
    is_deferrable: bool = False
    critical_load_kw: float = 0.0
    x: float = 0.0
    y: float = 0.0


class EnvironmentSpec(BaseModel):
    outage_windows: list[list[float]] = Field(default=[[19.0, 21.0], [6.0, 7.0]])
    irradiance_scale: float = 0.85
    horizon_hours: float = 24.0
    dt_min: int = 15


class EconomicsSpec(BaseModel):
    diesel_cost_rs_per_kwh: float = 22.0
    p2p_price_rs_per_kwh: float = 4.5
    p2p_transaction_charge_rs_per_kwh: float = 0.0
    wheeling_charge_rs_per_kwh: float = 0.0
    carbon_price_rs_per_tonne: float = 400.0
    voll_rs_per_kwh: float = 22.0
    discount_rate: float = 0.10
    project_life_years: int = 25
    discom_retail_rate: float = 6.5
    # Subsidies
    pm_surya_ghar: bool = True
    kusum_fls: bool = True
    bess_vgf_frac: float = 0.30
    rural_license_exempt: bool = True
    # Capex overrides
    solar_rs_per_kw: float = 45000.0
    bess_rs_per_kwh: float = 10000.0


class SimRequest(BaseModel):
    nodes: list[NodeSpec]
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    economics: EconomicsSpec = Field(default_factory=EconomicsSpec)


class TimeSeriesPoint(BaseModel):
    t: str
    grid_exchange_kw: float = 0.0
    shed_load_kw: float = 0.0
    curtailed_kw: float = 0.0
    islanded: bool = False
    node_data: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Topology-level results (from pandapower per step)
    vm_pu: dict[str, float] = Field(default_factory=dict)          # node_id → voltage (pu)
    line_flows_kw: dict[str, float] = Field(default_factory=dict)  # "M-S" → kW (+ = from→to)
    line_loading_pct: dict[str, float] = Field(default_factory=dict)


class TopologyEdge(BaseModel):
    name: str        # e.g. "M-S"
    from_node: str
    to_node: str
    length_km: float


class SimResponse(BaseModel):
    config_hash: str
    sim_seconds: float
    metrics: dict[str, Any]
    economics: dict[str, Any]
    timeseries: list[TimeSeriesPoint]
    viable_vs_retail: str  # "green" / "amber" / "red"
    topology: list[TopologyEdge] = Field(default_factory=list)


class EconRecalcRequest(BaseModel):
    config_hash: str
    economics: EconomicsSpec


class EconRecalcResponse(BaseModel):
    economics: dict[str, Any]
    viable_vs_retail: str
    recalc_ms: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_node_cfg(nodes: list[NodeSpec]) -> dict[str, dict]:
    cfg = {}
    for n in nodes:
        d: dict[str, Any] = {
            "pv_kw": n.pv_kw,
            "battery_kwh": n.battery_kwh,
            "avg_load_kw": n.avg_load_kw,
            "has_grid_port": n.has_grid_port,
            "node_type": n.node_type,
            "is_deferrable": n.is_deferrable,
            "critical_load_kw": n.critical_load_kw,
        }
        if n.generator_kw > 0:
            d["generator_kw"] = n.generator_kw
            d["gen_fuel_type"] = n.gen_fuel_type
            d["gen_min_load_frac"] = 0.25
        cfg[n.id] = d
    return cfg


def _build_market(econ: EconomicsSpec, node_cfg: dict) -> MarketConfig:
    return MarketConfig(
        name="interactive",
        node_cfg=node_cfg,
        diesel_cost_rs_per_kwh=econ.diesel_cost_rs_per_kwh,
        p2p_price_rs_per_kwh=econ.p2p_price_rs_per_kwh,
        p2p_transaction_charge_rs_per_kwh=econ.p2p_transaction_charge_rs_per_kwh,
        wheeling_charge_rs_per_kwh=econ.wheeling_charge_rs_per_kwh,
        carbon_price_rs_per_tonne=econ.carbon_price_rs_per_tonne,
        voll_rs_per_kwh=econ.voll_rs_per_kwh,
        subsidies=SubsidyStack(
            pm_surya_ghar=econ.pm_surya_ghar,
            kusum_fls=econ.kusum_fls,
            bess_vgf_frac=econ.bess_vgf_frac,
            rural_license_exempt=econ.rural_license_exempt,
        ),
        capex=CapexConfig(
            solar_rs_per_kw=econ.solar_rs_per_kw,
            bess_rs_per_kwh=econ.bess_rs_per_kwh,
        ),
        finance=FinanceConfig(
            discount_rate=econ.discount_rate,
            project_life_years=econ.project_life_years,
        ),
    )


def _verdict(viable_tariff: float, retail: float) -> str:
    if viable_tariff <= retail * 0.9:
        return "green"
    elif viable_tariff <= retail * 1.1:
        return "amber"
    return "red"


def _metrics_to_dict(m: Metrics) -> dict:
    return {
        "self_sufficiency_pct": round(m.self_sufficiency_pct, 1),
        "self_consumption_pct": round(m.self_consumption_pct, 1),
        "peak_grid_draw_kw": round(m.peak_grid_draw_kw, 2),
        "total_load_kwh": round(m.total_load_kwh, 1),
        "total_solar_kwh": round(m.total_solar_kwh, 1),
        "grid_import_kwh": round(m.grid_import_kwh, 1),
        "grid_export_kwh": round(m.grid_export_kwh, 1),
        "curtailed_kwh": round(m.curtailed_kwh, 1),
        "shed_load_kwh": round(m.shed_load_kwh, 1),
        "generator_fuel_L": round(m.generator_fuel_L, 1),
        "battery_cycles": {k: round(v, 2) for k, v in m.battery_cycles.items()},
    }


def _econ_to_dict(e: EconResult) -> dict:
    return {
        "capex_total_rs": round(e.capex_total_rs),
        "capex_after_subsidies_rs": round(e.capex_after_subsidies_rs),
        "subsidy_breakdown": {k: round(v) for k, v in e.subsidy_breakdown.items()},
        "annual_cost_rs": round(e.annual_cost_rs),
        "revenue_energy_sales_rs": round(e.revenue_energy_sales_rs),
        "revenue_p2p_rs": round(e.revenue_p2p_rs),
        "revenue_carbon_rs": round(e.revenue_carbon_rs),
        "cost_grid_import_rs": round(e.cost_grid_import_rs),
        "cost_fuel_rs": round(e.cost_fuel_rs),
        "cost_voll_penalty_rs": round(e.cost_voll_penalty_rs),
        "net_annual_rs": round(e.net_annual_rs),
        "payback_years": round(e.payback_years, 1) if e.payback_years < 1000 else None,
        "npv_rs": round(e.npv_rs),
        "lcoe_mesh_rs_per_kwh": round(e.lcoe_mesh_rs_per_kwh, 2),
        "viable_tariff_rs_per_kwh": round(e.viable_tariff_rs_per_kwh, 2),
    }


def _build_timeseries(results: SimResults) -> list[TimeSeriesPoint]:
    points = []
    for s in results.steps:
        nd: dict[str, dict[str, float]] = {}
        for nid, r in s.node_results.items():
            nd[nid] = {
                "solar_backplane_kw": round(r.solar_kw, 3),
                "load_kw":            round(r.load_kw, 3),
                "battery_kw":         round(r.battery_kw, 3),
                "soc_pct":            round(r.soc_pct, 1),
            }

        vm_pu = {k: round(v, 4) for k, v in s.pf_results.get("vm_pu", {}).items()}
        line_flows_kw = {
            k: round(v * 1000.0, 2)
            for k, v in s.line_flows_mw.items()
        }
        line_loading = {
            k: round(v, 1)
            for k, v in s.pf_results.get("line_loading_pct", {}).items()
        }

        points.append(TimeSeriesPoint(
            t=str(s.t),
            grid_exchange_kw=round(s.grid_exchange_kw, 3),
            shed_load_kw=round(s.shed_load_kw, 3),
            curtailed_kw=round(s.curtailed_kw, 3),
            islanded=s.islanded,
            node_data=nd,
            vm_pu=vm_pu,
            line_flows_kw=line_flows_kw,
            line_loading_pct=line_loading,
        ))
    return points


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/states")
def states():
    """Return all state economics/environment presets."""
    from src.state_configs import STATES, state_to_dict
    return {code: state_to_dict(s) for code, s in STATES.items()}


@app.get("/api/presets")
def presets():
    """Return the built-in market presets (rural / peri_urban / urban)."""
    from src.markets import MARKETS
    out = {}
    for key, m in MARKETS.items():
        nodes = []
        for nid, cfg in m.node_cfg.items():
            nodes.append({
                "id": nid,
                "node_type": cfg.get("node_type", "residential"),
                "pv_kw": cfg.get("pv_kw", 0.0),
                "battery_kwh": cfg.get("battery_kwh", 0.0),
                "avg_load_kw": cfg.get("avg_load_kw", 0.0),
                "has_grid_port": cfg.get("has_grid_port", False),
                "generator_kw": cfg.get("generator_kw", 0.0),
            })
        out[key] = {
            "name": m.name,
            "nodes": nodes,
            "outage_windows": m.outage_windows,
            "p2p_price": m.p2p_price_rs_per_kwh,
            "voll": m.voll_rs_per_kwh,
        }
    return out


@app.post("/api/simulate", response_model=SimResponse)
def simulate(req: SimRequest):
    """Run full physics simulation + economics. Caches results by config hash."""
    node_cfg = _build_node_cfg(req.nodes)
    env_dict = req.environment.model_dump()
    ch = _config_hash([n.model_dump() for n in req.nodes], env_dict)

    # Check cache (physics only depends on nodes + environment)
    if ch in _sim_cache:
        cached = _sim_cache[ch]
        results = cached["results"]
        metrics = cached["metrics"]
        sim_time = cached["sim_seconds"]
    else:
        t0 = time.time()

        # Generate profiles
        horizon_h = req.environment.horizon_hours
        dt_min = req.environment.dt_min
        n_days = max(1, int(np.ceil(horizon_h / 24)))
        daily_scales = generate_weather_sequence(n_days=n_days, season="mixed_week", seed=42)

        load_df = load_profiles(
            market="india_semi_urban",
            node_cfg=node_cfg,
            dt_min=dt_min,
            horizon_h=horizon_h,
            seed=42,
            start="2024-06-21",
        )
        pv_df = pv_profiles(
            node_cfg=node_cfg,
            dt_min=dt_min,
            horizon_h=horizon_h,
            irradiance_scale=req.environment.irradiance_scale,
            daily_irradiance_scales=daily_scales,
            seed=42,
            start="2024-06-21",
        )

        # Build outage steps
        dt_h = dt_min / 60.0
        outage_steps: set[int] = set()
        n_steps = int(horizon_h / dt_h)
        for step_i in range(n_steps):
            hour_of_day = (step_i * dt_h) % 24.0
            for ws, we in req.environment.outage_windows:
                if ws <= hour_of_day < we:
                    outage_steps.add(step_i)

        # Choose policy
        policy = OutagePreChargePolicy(
            outage_windows=[(ws, we) for ws, we in req.environment.outage_windows],
            pre_charge_minutes=30.0,
            gen_threshold_kw=0.2,
        )

        results = run_simulation(
            load_df, pv_df,
            policy=policy,
            node_cfg=node_cfg,
            grid_outage_steps=outage_steps,
        )
        metrics = compute_metrics(results)
        sim_time = time.time() - t0

        # Cache (evict oldest if full)
        if len(_sim_cache) >= MAX_CACHE:
            oldest = min(_sim_cache, key=lambda k: _sim_cache[k]["timestamp"])
            del _sim_cache[oldest]
        _sim_cache[ch] = {
            "results": results,
            "metrics": metrics,
            "sim_seconds": sim_time,
            "timestamp": time.time(),
            "node_cfg": node_cfg,
        }

    # Economics (always recomputed — it's fast)
    market = _build_market(req.economics, node_cfg)
    econ = compute_economics(results, market)

    # Expose all T1_STAR cables that actually had flow data (pandapower modelled them)
    from src.network import T1_STAR
    sample_flows = results.steps[0].line_flows_mw if results.steps else {}
    topo_edges = [
        TopologyEdge(name=f"{a}-{b}", from_node=a, to_node=b, length_km=lkm)
        for a, b, lkm in T1_STAR.cables
        if f"{a}-{b}" in sample_flows
    ]

    return SimResponse(
        config_hash=ch,
        sim_seconds=round(sim_time, 2),
        metrics=_metrics_to_dict(metrics),
        economics=_econ_to_dict(econ),
        timeseries=_build_timeseries(results),
        viable_vs_retail=_verdict(econ.viable_tariff_rs_per_kwh, req.economics.discom_retail_rate),
        topology=topo_edges,
    )


@app.post("/api/economics", response_model=EconRecalcResponse)
def recalc_economics(req: EconRecalcRequest):
    """Re-run economics only (no simulation). Uses cached SimResults."""
    if req.config_hash not in _sim_cache:
        from fastapi import HTTPException
        raise HTTPException(404, "Config not in cache — run /api/simulate first")

    cached = _sim_cache[req.config_hash]
    results = cached["results"]
    node_cfg = cached["node_cfg"]

    t0 = time.time()
    market = _build_market(req.economics, node_cfg)
    econ = compute_economics(results, market)
    elapsed_ms = (time.time() - t0) * 1000

    return EconRecalcResponse(
        economics=_econ_to_dict(econ),
        viable_vs_retail=_verdict(econ.viable_tariff_rs_per_kwh, req.economics.discom_retail_rate),
        recalc_ms=round(elapsed_ms, 1),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
