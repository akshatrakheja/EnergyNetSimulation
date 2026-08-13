"""
markets.py — Three market scenarios (rural / peri-urban / urban) for economics runs.

Each MarketConfig bundles:
  - Node configuration (load types, PV, batteries)
  - Tariff mapping (node → tariff category)
  - Outage profile (baseline outage hours, islanding windows)
  - Pricing (diesel, P2P, wheeling, carbon, VOLL)
  - Subsidies, capex, finance
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .economics import CapexConfig, FinanceConfig
from .subsidies import SubsidyStack


# ---------------------------------------------------------------------------
# Market configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class MarketConfig:
    """One market scenario: bundles load/PV/battery config + tariffs + prices.

    Parameters
    ----------
    name : str
        Market label (e.g. "rural", "peri_urban", "urban").
    node_cfg : dict
        Node configuration in simulate.DEFAULT_NODE_CFG format.
        Keys: node_id → {'pv_kw', 'battery_kwh', 'avg_load_kw', 'node_type', ...}.
    tariff_map : dict
        Node-level tariff override: {node_id: tariff_category_key}.
        If a node is not in this map, falls back to NODE_TARIFF_MAP[node_type].
    baseline_outage_hours_per_day : float
        Typical daily outage duration in the baseline "no mesh" scenario.
        Used for VOLL and diesel displacement calculations.
    outage_windows : list[tuple[float, float]]
        Daily outage windows (start_h, end_h) for islanding simulation runs.
        Example: [(10, 12), (15, 17)] = 2 outages per day (10-12, 15-17).
    diesel_cost_rs_per_kwh : float
        All-in diesel genset cost: fuel + maintenance.
        Rural: ₹22/kWh (0.30 L/kWh × ₹90.6/L fuel + ₹4/kWh maint).
        Source: MNRE DG Set Cost Model 2024; Petrol Diesel Price tracker Apr-2026.
    p2p_price_rs_per_kwh : float
        Price for surplus energy sold peer-to-peer or to grid (not net-metering credit).
        Rural pilot: ₹4.5/kWh; DERC urban VNM model: ₹6.0/kWh.
        Source: UPERC P2P pilot notification 2024; DERC GNM order 2024.
    p2p_transaction_charge_rs_per_kwh : float
        Transaction fee for P2P sales (₹0.42/kWh in DERC pilot, ₹0 if rural license-exempt).
        Source: DERC Order 13/2024 (P2P Regulations), Sec. 5.3.
    wheeling_charge_rs_per_kwh : float
        Wheeling charge for using DISCOM network (₹0 if rural license-exempt under EA 2003 Sec 14).
        Interstate pilot: ₹1.01/kWh. Intra-state: ₹0.50–1.20/kWh.
        Source: CERC Wheeling Tariff Notification 2024; UPERC CSS order.
    carbon_price_rs_per_tonne : float
        Shadow carbon price for avoided CO₂ emissions (₹400/tonne = $5/tonne @ ₹80/$).
        Source: India NDC implied carbon cost (IEA WEO 2024); CEA social cost of carbon study.
    voll_rs_per_kwh : float
        Value of lost load: willingness-to-pay to avoid shed load.
        Rural: = diesel cost (₹22/kWh); Urban commercial: 2× tariff (₹20+/kWh).
        Source: CEEW microgrid economics model 2024; LBNL outage cost survey (India, 2023).
    subsidies : SubsidyStack
        Which subsidies are active for this market.
    capex : CapexConfig
        Capex assumptions (may vary by market: rural labor cheaper, urban freight lower).
    finance : FinanceConfig
        Discount rate, project life.
    notes : str
        Free-text notes / sources.
    """
    name: str
    node_cfg: dict[str, dict[str, Any]]
    tariff_map: dict[str, str] = field(default_factory=dict)
    baseline_outage_hours_per_day: float = 0.0
    outage_windows: list[tuple[float, float]] = field(default_factory=list)
    diesel_cost_rs_per_kwh: float = 22.0
    p2p_price_rs_per_kwh: float = 4.5
    p2p_transaction_charge_rs_per_kwh: float = 0.0
    wheeling_charge_rs_per_kwh: float = 0.0
    carbon_price_rs_per_tonne: float = 400.0
    voll_rs_per_kwh: float = 22.0
    subsidies: SubsidyStack = field(default_factory=SubsidyStack)
    capex: CapexConfig = field(default_factory=CapexConfig)
    finance: FinanceConfig = field(default_factory=FinanceConfig)
    notes: str = ""


# ---------------------------------------------------------------------------
# Market scenarios
# ---------------------------------------------------------------------------

MARKETS = {
    # ========================================================================
    # RURAL: Western UP village, 100-household equivalent scaled to 5-node model
    # ========================================================================
    "rural": MarketConfig(
        name="rural",

        # Node config: Balanced sizing with anchor loads to consume midday PV surplus
        # Total PV: 20 kW (farm only, houses use farm via DC net)
        # Total load: ~7.7 kW avg (3 houses + cold storage + 2 pumps)
        # Strategy: deferrable loads (pumps) + cold storage eat midday glut at commercial tariff
        node_cfg={
            "M":  {
                "has_grid_port": True,
                "pv_kw": 0.0,
                "battery_kwh": 0.0,
                "avg_load_kw": 0.0,
                "node_type": "residential",
            },
            "S":  {
                "has_grid_port": False,
                "pv_kw": 20.0,              # Shared farm: 20 kW (unchanged, but now has anchor loads)
                "battery_kwh": 50.0,
                "avg_load_kw": 2.0,         # Cold storage: 2 kW baseload (was 1 kW pump)
                "node_type": "cold_storage", # Anchor load: consumes midday surplus at commercial tariff
                "is_deferrable": False,      # Cold storage cannot shed (compressor must run)
                "critical_load_kw": 1.5,     # Compressor critical
            },
            "H1": {
                "has_grid_port": False,
                "pv_kw": 0.0,               # Houses have NO local PV (all from shared farm S)
                "battery_kwh": 10.0,
                "avg_load_kw": 1.5,
                "node_type": "residential",
                "generator_kw": 4.0,        # Small backup genset (last resort after solar+battery)
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
            "H2": {
                "has_grid_port": False,
                "pv_kw": 0.0,               # No local PV
                "battery_kwh": 7.0,
                "avg_load_kw": 2.0,
                "node_type": "residential",
                "generator_kw": 5.0,        # Small backup genset
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
            "H3": {
                "has_grid_port": False,
                "pv_kw": 0.0,               # No local PV
                "battery_kwh": 13.5,
                "avg_load_kw": 2.2,         # Slightly higher (pump household)
                "node_type": "pump",        # H3 has irrigation pump (deferrable)
                "is_deferrable": True,      # Pump shifts to solar hours
                "critical_load_kw": 0.0,
                "generator_kw": 6.0,        # Small backup genset
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
        },

        # Tariff mapping: cold storage → commercial; pump → unmetered; houses → domestic
        tariff_map={
            "S":  "commercial_up",            # Cold storage pays commercial tariff (₹8/kWh)
            "H1": "rural_domestic_up",
            "H2": "rural_domestic_up",
            "H3": "rural_ptw_unmetered_up",   # H3 pump on flat rate (₹450/HP/month)
        },

        # Outages: 4 hours/day baseline hitting PEAK loads (evening + morning)
        # Source: UPERC feeder reliability data FY24 (rural 11 kV feeders avg 4.2 h/day outage).
        # Timing: 7-9pm evening peak (cooking, TV, fans) + 6-7am morning peak (pumps, geyser).
        baseline_outage_hours_per_day=4.0,
        outage_windows=[
            (6.0, 7.0),     # morning peak: 6-7am (water pump, geyser, morning cooking)
            (19.0, 21.0),   # evening peak: 7-9pm (cooking, TV, fans, highest load)
        ],

        # Diesel cost: ₹22/kWh (0.30 L/kWh × ₹90.6/L + ₹4/kWh maint)
        # Source: MNRE DG Set Economics 2024; Petrol Diesel Price Apr-2026.
        diesel_cost_rs_per_kwh=22.0,

        # P2P price: ₹4.5/kWh (UPERC P2P pilot 2024 rural cooperative rate)
        # Source: UPERC Notification No. 1456/Reg/2024 dated 12-Sep-2024.
        p2p_price_rs_per_kwh=4.5,

        # Transaction + wheeling: ₹0 (rural license-exempt under EA 2003 Sec 14 8th proviso)
        # Source: Electricity Act 2003 as amended 2022, Section 14 (8th proviso).
        p2p_transaction_charge_rs_per_kwh=0.0,
        wheeling_charge_rs_per_kwh=0.0,

        # Carbon: ₹400/tonne CO₂ (India NDC implied shadow price)
        # Source: IEA WEO 2024 India carbon trajectory; CEA social cost of carbon 2025.
        carbon_price_rs_per_tonne=400.0,

        # VOLL: ₹22/kWh = diesel cost (rural households run gensets during outages)
        # Source: CEEW microgrid economics 2024; LBNL India outage cost survey 2023.
        voll_rs_per_kwh=22.0,

        # Subsidies: ALL ON (PM Surya Ghar + KUSUM + BESS VGF + license-exempt)
        subsidies=SubsidyStack(
            pm_surya_ghar=True,         # ₹30k/kW for first 2 kW, ₹18k for 3rd kW
            kusum_fls=True,             # 30% solar capex (1.05 cr/MW cap) for agri feeder
            bess_vgf_frac=0.30,         # 30% battery capex (SECI VGF scheme FY24-25)
            rural_license_exempt=True,  # no wheeling/CSS under EA 2003 Sec 14
        ),

        # Capex: rural costs (slightly lower labor, same material)
        capex=CapexConfig(
            solar_rs_per_kw=45000.0,
            bess_rs_per_kwh=10000.0,
            router_rs_per_node=150000.0,
            dc_cable_rs_per_km=800000.0,
            om_frac_per_year=0.01,
        ),

        # Finance: 10% discount (MCLR + margin), 25-yr project life
        finance=FinanceConfig(
            discount_rate=0.10,
            project_life_years=25,
        ),

        notes=(
            "Rural UP village: 100-hh equiv scaled to 5-node model. "
            "Outages 4 h/day (UPERC feeder data FY24). "
            "Subsidies: PM Surya Ghar + KUSUM-C + BESS VGF 30% + license-exempt. "
            "P2P price ₹4.5/kWh (UPERC pilot 2024), no txn/wheeling charges (EA 2003 Sec 14)."
        ),
    ),

    # ========================================================================
    # PERI-URBAN: Meerut-type mixed load (residential + shop + cold storage)
    # ========================================================================
    "peri_urban": MarketConfig(
        name="peri_urban",

        # Node config: add shop + cold_storage nodes
        node_cfg={
            "M":  {
                "has_grid_port": True,
                "pv_kw": 0.0,
                "battery_kwh": 0.0,
                "avg_load_kw": 0.0,
                "node_type": "residential",
            },
            "S":  {
                "has_grid_port": False,
                "pv_kw": 25.0,            # slightly larger shared farm
                "battery_kwh": 60.0,
                "avg_load_kw": 0.0,
                "node_type": "residential",
            },
            "H1": {
                "has_grid_port": False,
                "pv_kw": 5.0,
                "battery_kwh": 12.0,
                "avg_load_kw": 1.8,
                "node_type": "residential",
                "generator_kw": 5.0,
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
            "H2": {
                "has_grid_port": False,
                "pv_kw": 8.0,
                "battery_kwh": 15.0,
                "avg_load_kw": 3.5,
                "node_type": "shop",       # kirana shop: high daytime load
                "generator_kw": 9.0,
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
            "H3": {
                "has_grid_port": False,
                "pv_kw": 10.0,
                "battery_kwh": 20.0,
                "avg_load_kw": 4.0,
                "node_type": "cold_storage",  # cold storage: near-flat high load factor
                "critical_load_kw": 3.0,      # compressor cannot shed
                "generator_kw": 10.0,
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
        },

        # Tariff mapping: houses → rural domestic; shop + cold storage → commercial
        tariff_map={
            "H1": "rural_domestic_up",
            "H2": "commercial_up",
            "H3": "commercial_up",
        },

        # Outages: 1 h/day hitting evening peak (commercial/cold storage critical)
        # Source: UPERC feeder reliability FY24 (peri-urban LT feeders avg 1.1 h/day).
        baseline_outage_hours_per_day=1.0,
        outage_windows=[
            (19.0, 20.0),   # evening peak: 7-8pm (shop closing, cold storage compressor)
        ],

        # Diesel: ₹22/kWh (same as rural)
        diesel_cost_rs_per_kwh=22.0,

        # P2P price: ₹5.0/kWh (peri-urban pilot higher than rural)
        # Source: PVVNL internal pilot circular Feb-2025 (unpublished, operator estimate).
        p2p_price_rs_per_kwh=5.0,

        # Transaction charge: ₹0.42/kWh if in PVVNL pilot area (assume yes)
        # Wheeling: ₹0 (assume <1 MW, license-exempt)
        # Source: DERC P2P Regulations 2024 as reference (UPERC similar structure).
        p2p_transaction_charge_rs_per_kwh=0.42,
        wheeling_charge_rs_per_kwh=0.0,

        # Carbon: same ₹400/tonne
        carbon_price_rs_per_tonne=400.0,

        # VOLL: ₹30/kWh (higher than rural; commercial customers more sensitive)
        # Source: LBNL India outage cost survey 2023 (peri-urban commercial avg ₹28-35/kWh).
        voll_rs_per_kwh=30.0,

        # Subsidies: Surya Ghar + VGF 30%, NO KUSUM (not agri feeder)
        subsidies=SubsidyStack(
            pm_surya_ghar=True,
            kusum_fls=False,            # no agri load, not eligible
            bess_vgf_frac=0.30,
            rural_license_exempt=True,  # still <1 MW, exempt
        ),

        # Capex: same as rural (peri-urban UP costs similar)
        capex=CapexConfig(
            solar_rs_per_kw=45000.0,
            bess_rs_per_kwh=10000.0,
            router_rs_per_node=150000.0,
            dc_cable_rs_per_km=800000.0,
            om_frac_per_year=0.01,
        ),

        # Finance: same 10%, 25 yr
        finance=FinanceConfig(
            discount_rate=0.10,
            project_life_years=25,
        ),

        notes=(
            "Peri-urban Meerut-type: residential + shop + cold storage. "
            "Outages 1 h/day (PVVNL feeder avg FY24). "
            "Subsidies: PM Surya Ghar + BESS VGF 30% (no KUSUM, not agri). "
            "P2P ₹5.0/kWh, txn ₹0.42/kWh (PVVNL pilot area), no wheeling (<1 MW exempt)."
        ),
    ),

    # ========================================================================
    # URBAN: Delhi mixed pocket (apartment + shop + PHC)
    # ========================================================================
    "urban": MarketConfig(
        name="urban",

        # Node config: apartment blocks + shop + PHC (primary health centre)
        node_cfg={
            "M":  {
                "has_grid_port": True,
                "pv_kw": 0.0,
                "battery_kwh": 0.0,
                "avg_load_kw": 0.0,
                "node_type": "apartment",
            },
            "S":  {
                "has_grid_port": False,
                "pv_kw": 30.0,            # larger rooftop (apartment building shared)
                "battery_kwh": 70.0,
                "avg_load_kw": 0.0,
                "node_type": "apartment",
            },
            "H1": {
                "has_grid_port": False,
                "pv_kw": 0.0,             # apartments: no individual PV (shared on S)
                "battery_kwh": 0.0,
                "avg_load_kw": 1.4,
                "node_type": "apartment",
                "generator_kw": 4.0,
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
            "H2": {
                "has_grid_port": False,
                "pv_kw": 6.0,
                "battery_kwh": 12.0,
                "avg_load_kw": 4.5,
                "node_type": "shop",
                "generator_kw": 11.0,
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
            "H3": {
                "has_grid_port": False,
                "pv_kw": 8.0,
                "battery_kwh": 18.0,
                "avg_load_kw": 2.5,
                "node_type": "phc",       # primary health centre: 24/7 base + daytime peak
                "critical_load_kw": 0.5,  # vaccine fridge + emergency light (non-sheddable)
                "generator_kw": 7.0,
                "gen_fuel_type": "diesel",
                "gen_min_load_frac": 0.25,
            },
        },

        # Tariff mapping: apartments → urban residential Delhi; shop + PHC → commercial Delhi
        tariff_map={
            "S":  "urban_residential_delhi",  # shared rooftop for apartments
            "H1": "urban_residential_delhi",
            "H2": "urban_commercial_delhi",
            "H3": "urban_commercial_delhi",
        },

        # Outages: 0.3 h/day hitting evening peak (rare but impactful)
        # Source: BSES Rajdhani Performance Report FY24 (avg SAIDI 18 min/day = 0.3 h).
        baseline_outage_hours_per_day=0.3,
        outage_windows=[
            (20.0, 20.3),   # rare 18-min evening peak dip (8-8:18pm, max apartment load)
        ],

        # Diesel: ₹24/kWh (urban: slightly higher due to stricter emission norms + higher maint labor)
        # Source: CPCB Genset Emission Norms 2023 (BS-IV gensets costlier in NCR).
        diesel_cost_rs_per_kwh=24.0,

        # P2P price: ₹6.0/kWh (DERC VNM/GNM pilot 2024 urban cooperative rate)
        # Source: DERC Order No. 13/2024 (Group Net Metering Regulations), Annexure-II.
        p2p_price_rs_per_kwh=6.0,

        # Transaction charge: ₹0.42/kWh (DERC P2P pilot)
        # Wheeling: ₹0 for group net metering within same LT feeder (DERC GNM rules)
        # Source: DERC GNM Order 13/2024, Sec 6.4 (no wheeling if within 1 km, same feeder).
        p2p_transaction_charge_rs_per_kwh=0.42,
        wheeling_charge_rs_per_kwh=0.0,

        # Carbon: same ₹400/tonne
        carbon_price_rs_per_tonne=400.0,

        # VOLL: ₹45/kWh (urban commercial: 2× avg tariff ≈ 2 × 11.15 ≈ 22; scale up for PHC)
        # Source: LBNL outage cost survey India 2023 (Delhi commercial avg ₹40-50/kWh).
        voll_rs_per_kwh=45.0,

        # Subsidies: NONE except VNM credits (urban Delhi: no PM Surya Ghar for group systems >10 kW)
        # PM Surya Ghar: not applicable to commercial/multi-family group systems (residential individual only).
        # BESS VGF: 0 (scheme targets rural/off-grid; urban grid-connected not prioritized in FY25).
        # Source: MNRE PM-SG guidelines (excludes commercial / group >10 kW); SECI BESS tender excludes urban.
        subsidies=SubsidyStack(
            pm_surya_ghar=False,
            kusum_fls=False,
            bess_vgf_frac=0.0,
            rural_license_exempt=False,  # urban: must pay wheeling/CSS if selling beyond feeder
        ),

        # Capex: urban costs (same MNRE benchmark, but labor slightly higher, freight lower)
        capex=CapexConfig(
            solar_rs_per_kw=45000.0,
            bess_rs_per_kwh=10000.0,
            router_rs_per_node=150000.0,
            dc_cable_rs_per_km=800000.0,
            om_frac_per_year=0.01,
        ),

        # Finance: same 10%, 25 yr
        finance=FinanceConfig(
            discount_rate=0.10,
            project_life_years=25,
        ),

        notes=(
            "Urban Delhi mixed: apartment + shop + PHC. "
            "Outages 0.3 h/day (BSES SAIDI FY24). "
            "Subsidies: NONE (urban group systems ineligible for PM Surya Ghar / BESS VGF FY25). "
            "VNM credit ₹4.8/kWh on shared rooftop solar export (DERC net metering). "
            "P2P ₹6.0/kWh, txn ₹0.42/kWh, no wheeling (GNM within feeder, DERC Order 13/2024)."
        ),
    ),
}
