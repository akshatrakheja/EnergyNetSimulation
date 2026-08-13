# EnergyNet — Community DC Microgrid Simulation

> **Phase 1** (energy router power electronics, OpenModelica):
> [github.com/akshatrakheja/EnergyNetPhase1](https://github.com/akshatrakheja/EnergyNetPhase1)

## What EnergyNet is

EnergyNet is an Internet-inspired architecture for electricity distribution. Where the
telephone network was transformed in the 1990s by moving from centralized circuit switching
to decentralized packet switching, EnergyNet proposes the same transition for power: a
modular, software-defined distribution layer built around energy routers, DC backplane
cables, and an open Energy Protocol for inter-domain negotiation.

The three building blocks are an **Energy Router** (galvanic-isolated ports, DC bus,
software-controlled power flows), **ELAN / EWAN boundaries** (local and wide-area energy
networks, analogous to LAN / WAN), and a **control plane** consisting of the Energy Router
Operating System (EROS) and an Energy Network Management System (ENMS). The architecture
is described in full in:

> Birgersson et al., "EnergyNet Explained: Internetification of Energy Distribution,"
> arXiv:2509.08152 (2025).
> [https://arxiv.org/abs/2509.08152](https://arxiv.org/abs/2509.08152)

The world's first operational EnergyNet installation launched on 26 April 2025 in Lund,
Sweden — a parallel DC microgrid connecting two buildings via a "Freedom Cable" in the
Brunnshög Innovation District.

---

## Phase 1 — What it established

Phase 1 validated a single energy router at the power-electronics layer using OpenModelica.

| Port                  | Role                  | P_max | Peak η |
| --------------------- | --------------------- | ----- | ------ |
| Peer (DAB, DC–DC)     | Router-to-router link | 5 kW  | 0.989  |
| Battery (buck-boost)  | Local storage         | 5 kW  | 0.980  |
| Solar (LLC, isolated) | PV input              | 5 kW  | 0.976  |
| Grid (full AC stack)  | Mothership grid tie   | 5 kW  | 0.937  |

Port efficiency curves η(P), settling times τ, and standby draws enter Phase 2 as lumped
constants — the quasi-static assumption is justified by Phase 1's sub-20ms τ measurements.

---

## Phase 2 — This repository

Phase 2 zooms out by roughly three orders of magnitude and simulates a **community of
routers** over hours to weeks. The canonical topology is five nodes — three houses (H1, H2,
H3), a shared solar farm (S), and a mothership router (M) at the grid boundary —
connected by DC cables, running quasi-static power-flow at each 15-minute timestep.

The simulation is two layers:

- **Layer A — Dispatch:** pluggable policies (local-greedy, community-buffering, EV-aware
  V2G, generator-aware, demand-response) set battery setpoints each step.
- **Layer B — Network:** [pandapower](https://www.pandapower.org/) solves DC power flow,
  yielding cable currents, I²R losses, bus voltages, and the grid exchange at the mothership.

**Scenarios (S0–S7):** sunny baseline, partial PV failure, battery sizing sweeps, seasonal
variation, community vs. greedy dispatch, large-scale replication, grid cap sweep, and
islanding with shed-load tracking.

**Topologies (T1, T4, T5, T6, TSSPOKE, TDCBUS):** star, ring backplane, V2G with
bidirectional EV chargers, apartment block, solar-farm-spoke, and shared DC bus.

**Load shapes:** nine verified entity-type profiles — residential, shop, cold storage,
telecom tower, school, irrigation pump, street lighting, primary health centre, and
apartment — calibrated against Prayas eMARC smart-meter data, BEE cluster audits, TRAI
tower energy surveys, and MNRE rural facility guidelines.

---

## Economics & Market Layer

The financial layer computes a full DISCOM pitch pro-forma for each simulation run.

**Three canonical market configs** (`markets.py`):

| Code | Context       | Anchor loads               | Typical outage |
|------|---------------|----------------------------|----------------|
| E1   | Rural         | Houses + pump + cold store | 6–8 h/day      |
| E2   | Peri-urban    | Houses + shop + tower      | 2–4 h/day      |
| E3   | Urban         | Apartments + commercial    | 0.5–1.5 h/day  |

**Per-run outputs** (`economics.py`):
- CAPEX breakdown: solar, BESS, routers, cable — India 2026 EPC rates (MNRE benchmark)
- Subsidy stack: PM Surya Ghar (₹18,000 central + state top-up), KUSUM FLS, BESS VGF (up to 40%)
- Revenue streams: P2P energy sales, diesel displacement, demand charge savings, carbon credit
- LCOE · LCOS · payback · NPV · viable tariff vs. DISCOM retail — **green / amber / red** viability flag

**Lever sweeps** (`econ_scenarios.py`): subsidy on/off × P2P price × battery scale matrices saved to `data/results/econ/`.

**Tariff schedule** (`tariffs.py`): time-of-use slabs, fixed charges, and demand charges modeled for LT domestic, LT commercial, agricultural, and bulk supply categories.

---

## State Presets

`state_configs.py` encodes FY 2025-26 regulatory and physical parameters for **seven Indian states**, each sourced from the relevant SERC tariff order:

| Code | State             | DISCOM                        | Region               |
|------|-------------------|-------------------------------|----------------------|
| DL   | Delhi             | BSES Rajdhani/Yamuna · TPDDL  | North India (Urban)  |
| JH   | Jharkhand         | JBVNL                         | East India           |
| HP   | Himachal Pradesh  | HPSEBL                        | North India (Hills)  |
| MH   | Maharashtra       | MSEDCL                        | West India           |
| MP   | Madhya Pradesh    | MPPKVVCL / MPMKVVCL           | Central India        |
| KL   | Kerala            | KSEB                          | South India          |
| KA   | Karnataka         | BESCOM / GESCOM               | South India          |

Each preset carries domestic/commercial/agri tariffs, rural and urban outage hours, GHI, P2P ceiling, wheeling charges, VOLL, subsidy flags, state-adjusted capex, and a pitch narrative. Selecting a state in the GUI hot-loads all economics and environment sliders.

---

## Web GUI

A browser-based simulator built with **React + Vite** (frontend) and **FastAPI** (backend).

**How it works:**
1. Drag entities from the palette onto the canvas to build a microgrid topology.
2. Click an entity to set its PV, battery, load, and genset parameters.
3. Pick a State preset to load state-calibrated economics and outage windows.
4. Adjust economics sliders (tariff, diesel, P2P price, VOLL, discount rate, subsidies).
5. Click **Run Simulation** — physics runs once and is cached by config hash.
6. Scrub the animated playback: flows animate on edges, islanded nodes highlight, a results panel shows LCOE, payback, NPV, and the green/amber/red viability flag.
7. Moving an economics slider triggers a fast recalculation (<50 ms) without re-running physics.

**Entity palette:** House · Apartment · Cold Store · Shop · Pump · Telecom Tower · Solar Farm · Grid Meter · Genset

**Two-tier backend:** physical config hash → full simulation (~15 s); economics-only change → recompute in <50 ms from cached physics results.

---

## DEG / Beckn P2P Integration

`deg_adapter.py` bridges the Phase 2 simulator to the **DEG wave2 devkit** (Beckn-protocol P2P energy trading).

Flow:
1. Runs the E1 rural simulation (7-day, 15-min steps).
2. Aggregates 15-min grid-export intervals → 1-hour BecknTimeSeries slots.
3. POSTs `confirm` to the wave2 BPP caller (seller side, port 8082).
4. POSTs `on_status` with actuals and parses `revenueFlows` from the response.
5. Cross-checks DEG-computed P2P settlement against `economics.py` revenue.

```bash
python deg_adapter.py [--start] [--market rural|peri_urban|urban] [--verbose]
```

Devkit endpoints (localhost): BAP caller :8081 · BPP caller :8082 · Seller DISCOM BPP :8083 · Buyer DISCOM BPP :8084 · Caddy full-stack :9000.

---

## Repository layout

```
src/
  battery.py        Battery model (SoC, charge/discharge limits)
  dispatch.py       Dispatch policies (greedy, community, EV-aware, DR, generator-aware)
  econ_scenarios.py Economic runners E1/E2/E3 with lever sweeps; saves to data/results/econ/
  economics.py      CAPEX · subsidy · revenue · LCOE/NPV/payback computation
  generator.py      Diesel/biogas genset — fuel consumption curve (BEE 2021)
  markets.py        Rural / peri-urban / urban MarketConfig bundles
  metrics.py        Post-run metrics (SS%, SC%, losses, shed load, fuel)
  network.py        pandapower network builder; TopologyConfig system
  ports.py          Port efficiency curves η(P) from Phase 1 datasheet
  profiles.py       Load and PV profile generation; entity-type load shape catalogue
  router.py         Per-node energy accounting (Layer A physics)
  scenarios.py      Scenario runners S0–S7, T1–T6, topology comparison, grid cap sweep
  simulate.py       Main simulation loop (two-layer architecture)
  state_configs.py  FY 2025-26 state presets (DL/JH/HP/MH/MP/KL/KA)
  subsidies.py      PM Surya Ghar · KUSUM · BESS VGF subsidy stack
  tariffs.py        TOU tariff schedules — energy, fixed, and demand charges

app/
  backend/
    server.py       FastAPI server — /simulate, /recalc_economics, /states endpoints
  frontend/
    src/
      components/
        Canvas.tsx        Drag-and-drop topology editor + animated playback
        Palette.tsx       Entity drag palette
        ResultsPanel.tsx  Economics + metrics display
        Sidebar.tsx       Sliders and toggles; Run button
        StatePresets.tsx  State selector strip with inline detail
      store.ts            Zustand state (nodes, environment, economics, sim results)
      api.ts              Fetch wrappers for backend endpoints

deg_adapter.py      Beckn DEG wave2 devkit integration
data/results/       Saved CSV timeseries + TXT summaries
```

---

## Running

**Backend:**
```bash
pip install fastapi uvicorn pandapower numpy pandas scipy
uvicorn app.backend.server:app --reload --port 8000
```

**Frontend:**
```bash
cd app/frontend
npm install
npm run dev          # → http://localhost:5173
```

**Simulation only (no GUI):**
```python
from src.scenarios import run_all_scenarios
run_all_scenarios()
```

**Economic scenarios only:**
```python
from src.econ_scenarios import run_all_econ_scenarios
run_all_econ_scenarios()   # outputs to data/results/econ/
```

---

## References

- Birgersson et al., "EnergyNet Explained," arXiv:2509.08152 (2025). [https://arxiv.org/abs/2509.08152](https://arxiv.org/abs/2509.08152)
- EnergyNet Task Force: [https://github.com/energyetf/energynet](https://github.com/energyetf/energynet)
- Prayas (Energy Group), "Electricity Load Patterns," eMARC Dataset, July 2021.
- BEE / MNRE, "Efficient Operation of Diesel Generating Sets," 2021.
- Sameeeksha / BEE, "Hooghly Cold Storage Cluster Energy Profile," 2018.
- TRAI / Intelligent Energy, "The True Cost of Providing Energy for Telecom Towers in India," 2013.
- MNRE, "Rural Health Facility Electrification Guidelines," 2019.
- Ministry of Power, GoI, Rajya Sabha Q.1031, 9 February 2026. [https://powermin.gov.in/sites/default/files/uploads/RS09022026_Eng.pdf](https://powermin.gov.in/sites/default/files/uploads/RS09022026_Eng.pdf)
- Build Log (Phase 1): [https://ribbon-tango-f09.notion.site/BUILD-LOG-37d37b00f67f80048cfbc9450ccd502a](https://ribbon-tango-f09.notion.site/BUILD-LOG-37d37b00f67f80048cfbc9450ccd502a?source=copy_link)

[^1]: Ministry of Power, GoI, Rajya Sabha Unstarred Question No. 1031, answered 9 February 2026.
