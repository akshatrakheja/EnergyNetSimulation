# EnergyNet — Community DC Microgrid Simulation

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
> Also published at: [https://research.chalmers.se/en/publication/548200](https://research.chalmers.se/en/publication/548200)

The open specification and reference implementation are maintained by the EnergyNet Task
Force at [https://github.com/energyetf/energynet](https://github.com/energyetf/energynet).

The world's first operational EnergyNet installation launched on 26 April 2025 in Lund,
Sweden — a parallel DC microgrid connecting two buildings via a "Freedom Cable" in the
Brunnshög Innovation District.

---

## Phase 1 — What it established

Phase 1 validated a single energy router at the power-electronics layer (microseconds to
milliseconds) using OpenModelica. Four converter ports were characterized:


| Port                  | Role                  | P_max | Peak η |
| --------------------- | --------------------- | ----- | ------ |
| Peer (DAB, DC–DC)     | Router-to-router link | 5 kW  | 0.989  |
| Battery (buck-boost)  | Local storage         | 5 kW  | 0.980  |
| Solar (LLC, isolated) | PV input              | 5 kW  | 0.976  |
| Grid (full AC stack)  | Mothership grid tie   | 5 kW  | 0.937  |


Each port's efficiency curve η(P), settling time τ, and standby draw were exported as a
compact datasheet. Because every port settles in under 20 ms and dispatch decisions are
made on timescales of seconds to minutes, these parameters enter Phase 2 as lumped
constants — the quasi-static assumption is justified by Phase 1's τ measurements, not
assumed.

---

## Phase 2 — This repository

Phase 2 zooms out by roughly three orders of magnitude in time and simulates a
**community of routers** over hours to weeks. The canonical topology is five nodes —
three houses (H1, H2, H3), a shared solar farm (S), and a mothership router (M) at the
grid boundary — connected by DC cables, running quasi-static power-flow at each 15-minute
timestep.

The simulation is structured as two layers:

- **Layer A — Dispatch:** A pluggable policy decides battery setpoints for each node at
each step. Policies range from local-greedy to community-buffering to EV-aware V2G, with
a demand-response variant that shifts deferrable loads (irrigation pumps) to coincide
with solar surplus.
- **Layer B — Network:** [pandapower](https://www.pandapower.org/) solves the DC
power flow, yielding cable currents, I²R losses, bus voltages, and the grid exchange
absorbed by the mothership VSC.

### Simulation scope

- **Scenarios (S0–S7):** sunny baseline, partial PV failure, battery sizing sweeps,
seasonal variation, community vs. greedy dispatch, large-scale replication, grid cap
sweep, and islanding (grid outage with shed-load tracking).
- **Topologies (T1, T4, T5, T6, TSSPOKE, TDCBUS):** star, ring backplane, V2G
with bidirectional EV chargers, apartment block, solar-farm-spoke, and shared DC bus.
- **Load shapes:** nine verified entity-type profiles — residential, shop, cold storage,
telecom tower, school, irrigation pump, street lighting, primary health centre, and
apartment — calibrated against Prayas eMARC smart-meter data, BEE cluster audits, TRAI
tower energy surveys, and MNRE rural facility guidelines.
- **Generator model:** a diesel genset (or biogas) class with a specific fuel consumption
curve calibrated to BEE efficiency guidelines (2021) for 10–30 kW sets.
- **Multi-day runs:** up to seven days with per-day weather sequences (summer, monsoon,
post-monsoon, winter, hazy) drawn from representative north/central India irradiance
patterns.

### Indian context

The load shapes, weather sequences, regulation research (net metering caps, Virtual Net
Metering, Group Net Metering, P2P trading pilots), and scenario design are oriented toward
semi-urban and rural India. Average electricity supply in Indian rural areas reached
22.6 hours per day in FY2025, up from 12.5 hours in 2014 — but reliability and last-mile
quality remain active problems, with 13.65 lakh households still sanctioned for grid
electrification under the Revamped Distribution Sector Scheme as of early
2026.[^1] Distributed DC microgrids of the kind EnergyNet proposes are a direct
architectural response to this gap.

[^1]: Ministry of Power, Government of India, Rajya Sabha Unstarred Question No. 1031,
answered 9 February 2026.
[https://powermin.gov.in/sites/default/files/uploads/RS09022026_Eng.pdf](https://powermin.gov.in/sites/default/files/uploads/RS09022026_Eng.pdf)

---

## Repository layout

```
src/
  battery.py       Battery model (SoC, charge/discharge limits)
  dispatch.py      Dispatch policies (greedy, community, EV-aware, generator-aware, DR)
  generator.py     Dispatchable generator — diesel / biogas, fuel consumption model
  metrics.py       Post-run metric computation (SS%, SC%, losses, shed load, fuel)
  network.py       pandapower network builder; TopologyConfig system
  ports.py         Port efficiency curves η(P) from Phase 1 datasheet
  profiles.py      Load and PV profile generation; entity-type load shape catalogue
  router.py        Per-node energy accounting (Layer A physics)
  scenarios.py     Scenario runners (S0–S7, T1–T6, topology comparison, grid cap sweep)
  simulate.py      Main simulation loop (two-layer architecture)

data/results/      Saved simulation outputs (CSV timeseries + TXT summaries)
```

---

## Dependencies

Python 3.11+, pandapower, numpy, pandas, scipy. Install with:

```bash
pip install pandapower numpy pandas scipy
```

Run all scenarios:

```python
from src.scenarios import run_all_scenarios
run_all_scenarios()
```

---

## References

- Birgersson et al., "EnergyNet Explained: Internetification of Energy Distribution,"
arXiv:2509.08152 (2025). [https://arxiv.org/abs/2509.08152](https://arxiv.org/abs/2509.08152)
- EnergyNet Task Force specification: [https://github.com/energyetf/energynet](https://github.com/energyetf/energynet)
- Prayas (Energy Group), "Electricity Load Patterns," eMARC Dataset, July 2021.
- BEE / MNRE, "Efficient Operation of Diesel Generating Sets," 2021.
- Sameeeksha / BEE, "Hooghly Cold Storage Cluster Energy Profile," 2018.
[https://sameeeksha.org/pdf/Hooghly%20Cold%20Storage%20Cluster%20Profile.pdf](https://sameeeksha.org/pdf/Hooghly%20Cold%20Storage%20Cluster%20Profile.pdf)
- TRAI / Intelligent Energy, "The True Cost of Providing Energy for Telecom Towers in
India," 2013.
- MNRE, "Rural Health Facility Electrification Guidelines," 2019.
- Ministry of Power, GoI, Rajya Sabha Q.1031, 9 February 2026.
[https://powermin.gov.in/sites/default/files/uploads/RS09022026_Eng.pdf](https://powermin.gov.in/sites/default/files/uploads/RS09022026_Eng.pdf)
- Build Log for phase 1 by our team: [https://ribbon-tango-f09.notion.site/BUILD-LOG-37d37b00f67f80048cfbc9450ccd502a?source=copy_link](https://ribbon-tango-f09.notion.site/BUILD-LOG-37d37b00f67f80048cfbc9450ccd502a?source=copy_link)

