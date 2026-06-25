"""
router.py — Layer A: per-router local energy accounting.

A Router's job each timestep:
  1. Apply solar port η to convert available PV power → DC backplane power.
  2. Apply home battery port η for charge/discharge losses.
  3. Optionally apply EV battery port η (if V2G / T5 scenario).
  4. Compute the single net DC bus power pandapower (Layer B) will see.
  5. Return losses and battery state updates.

What the Router does NOT do:
  - Does not decide peer flows (those emerge from pandapower physics).
  - Does not model grid exchange (the VSC slack handles it in Layer B).
  - Does not implement dispatch policy logic (lives in dispatch.py).

EV battery notes:
  - If RouterConfig.ev_battery is set, the node participates in T5 (V2G).
  - The ev_battery_kw setpoint passed to step() is +charge / −discharge.
  - The Router enforces max power and SoC constraints but does NOT enforce
    departure-time logic — that is the dispatch policy's responsibility.
  - When ev_plugged_in=False, ev_battery_kw is forced to 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .battery import Battery
from .generator import Generator
from .ports import P_MAX_W, P_STANDBY_W, eta

P_MAX_KW: dict[str, float] = {k: v / 1000.0 for k, v in P_MAX_W.items()}
P_STANDBY_KW: dict[str, float] = {k: v / 1000.0 for k, v in P_STANDBY_W.items()}

EV_P_MAX_KW: float = 7.0   # 7 kW AC/DC bidirectional charger
EV_ETA: float = 0.92       # round-trip efficiency per direction


@dataclass
class RouterConfig:
    node_id: str
    pv_peak_kw: float = 0.0
    battery: Battery | None = None
    has_grid_port: bool = False
    eta_load: float = 1.0
    # EV (T5 / V2G)
    ev_battery: Battery | None = None
    ev_p_max_kw: float = EV_P_MAX_KW
    # Dispatchable generator (diesel genset / biogas)
    generator: Generator | None = None
    # Critical (non-sheddable) load in kW.
    # Used by the islanding logic in simulate.py to determine what fraction
    # of unserved demand is truly critical (e.g. vaccine fridge, emergency light).
    critical_load_kw: float = 0.0


@dataclass
class RouterStepResult:
    """Outputs from Router.step()."""
    net_bus_kw: float

    solar_kw: float
    load_kw: float
    battery_kw: float        # home battery: + charge, − discharge
    ev_battery_kw: float     # EV battery:   + charge, − discharge (0 if no EV)
    generator_kw: float      # generator actual output (0 if no generator)
    curtailed_kw: float

    solar_loss_kw: float
    battery_loss_kw: float
    ev_battery_loss_kw: float
    standby_loss_kw: float

    # Home battery state
    soc_kwh: float
    soc_pct: float
    battery_eta: float

    # EV battery state (0.0 if no EV)
    ev_soc_kwh: float
    ev_soc_pct: float

    # Generator state (0.0 / False if no generator)
    generator_fuel_step_L: float
    generator_running: bool


class Router:

    def __init__(self, config: RouterConfig) -> None:
        self.cfg = config

    @property
    def node_id(self) -> str:
        return self.cfg.node_id

    @property
    def battery(self) -> Battery | None:
        return self.cfg.battery

    @property
    def ev_battery(self) -> Battery | None:
        return self.cfg.ev_battery

    @property
    def generator(self) -> Generator | None:
        return self.cfg.generator

    def battery_headroom_kw(self) -> float:
        b = self.cfg.battery
        if b is None:
            return 0.0
        return min(P_MAX_KW["battery"], b.max_charge_kw())

    def battery_available_kw(self) -> float:
        b = self.cfg.battery
        if b is None:
            return 0.0
        return min(P_MAX_KW["battery"], b.max_discharge_kw())

    def ev_headroom_kw(self) -> float:
        b = self.cfg.ev_battery
        if b is None:
            return 0.0
        return min(self.cfg.ev_p_max_kw, b.max_charge_kw())

    def ev_available_kw(self) -> float:
        b = self.cfg.ev_battery
        if b is None:
            return 0.0
        return min(self.cfg.ev_p_max_kw, b.max_discharge_kw())

    def step(
        self,
        solar_available_kw: float,
        load_kw: float,
        battery_kw: float,
        dt_h: float = 0.25,
        ev_battery_kw: float = 0.0,
        ev_plugged_in: bool = True,
        generator_kw: float = 0.0,
    ) -> RouterStepResult:
        """Advance one timestep.

        Parameters
        ----------
        solar_available_kw : float
            Raw PV power at panel terminals (kW).
        load_kw : float
            House load demand (kW, ≥ 0).
        battery_kw : float
            Home battery setpoint: + charge, − discharge.
        dt_h : float
            Timestep in hours.
        ev_battery_kw : float
            EV battery setpoint: + charge, − discharge.
            Ignored (forced 0) if ev_plugged_in=False or no EV configured.
        ev_plugged_in : bool
            Whether the EV is physically at home and plugged in.
        generator_kw : float
            Requested generator output (kW ≥ 0).  The Generator object clamps
            this to [p_min, p_rated] if running, or 0 if not requested.
            Ignored if no generator is configured.
        """
        cfg = self.cfg

        # --- Solar port ---
        solar_available_kw = max(0.0, solar_available_kw)
        if solar_available_kw > 0:
            eta_s = eta("solar", solar_available_kw * 1000.0)
            solar_backplane = solar_available_kw * eta_s
            solar_loss = solar_available_kw - solar_backplane
            solar_standby_kw = 0.0
        else:
            solar_backplane = 0.0
            solar_loss = 0.0
            solar_standby_kw = P_STANDBY_KW["solar"]

        # --- Home battery port ---
        battery_kw = _clamp(battery_kw, -P_MAX_KW["battery"], P_MAX_KW["battery"])
        if cfg.battery is not None:
            if battery_kw > 0:
                battery_kw = min(battery_kw, self.battery_headroom_kw())
            else:
                battery_kw = max(battery_kw, -self.battery_available_kw())
        else:
            battery_kw = 0.0

        batt_loss = _port_loss(battery_kw)
        batt_standby_kw = P_STANDBY_KW["battery"] if cfg.battery is not None else 0.0

        # --- EV battery port ---
        if cfg.ev_battery is None or not ev_plugged_in:
            ev_battery_kw = 0.0
            ev_loss = 0.0
        else:
            ev_battery_kw = _clamp(ev_battery_kw, -cfg.ev_p_max_kw, cfg.ev_p_max_kw)
            if ev_battery_kw > 0:
                ev_battery_kw = min(ev_battery_kw, self.ev_headroom_kw())
            else:
                ev_battery_kw = max(ev_battery_kw, -self.ev_available_kw())
            ev_loss = _port_loss_ev(ev_battery_kw)

        # --- Generator port ---
        gen_result: dict = {}
        if cfg.generator is not None:
            gen_result = cfg.generator.step(max(0.0, generator_kw), dt_h)
            actual_gen_kw = gen_result["actual_kw"]
        else:
            actual_gen_kw = 0.0

        # --- Standby draws ---
        peer_standby_kw = P_STANDBY_KW["peer"]
        grid_standby_kw = P_STANDBY_KW["grid"] if cfg.has_grid_port else 0.0
        total_standby_kw = (
            solar_standby_kw + batt_standby_kw + peer_standby_kw + grid_standby_kw
        )

        # --- Net bus power (Layer B input) ---
        battery_charge    = max(0.0,  battery_kw)
        battery_discharge = max(0.0, -battery_kw)
        ev_charge         = max(0.0,  ev_battery_kw)
        ev_discharge      = max(0.0, -ev_battery_kw)

        net_bus_kw = (
            solar_backplane
            + battery_discharge
            + ev_discharge
            + actual_gen_kw
            - load_kw
            - battery_charge
            - ev_charge
            - total_standby_kw
        )

        # --- Update home battery SoC ---
        batt_result: dict = {}
        if cfg.battery is not None and abs(battery_kw) > 1e-9:
            batt_result = cfg.battery.step(battery_kw, dt_h)
        elif cfg.battery is not None:
            batt_result = {
                "soc_kwh": cfg.battery.soc_kwh,
                "soc_pct": cfg.battery.soc_pct,
                "eta": 0.0,
            }

        # --- Update EV battery SoC ---
        ev_result: dict = {}
        if cfg.ev_battery is not None and abs(ev_battery_kw) > 1e-9:
            ev_result = cfg.ev_battery.step(ev_battery_kw, dt_h)
        elif cfg.ev_battery is not None:
            ev_result = {
                "soc_kwh": cfg.ev_battery.soc_kwh,
                "soc_pct": cfg.ev_battery.soc_pct,
            }

        return RouterStepResult(
            net_bus_kw=net_bus_kw,
            solar_kw=solar_backplane,
            load_kw=load_kw,
            battery_kw=battery_kw,
            ev_battery_kw=ev_battery_kw,
            generator_kw=actual_gen_kw,
            curtailed_kw=0.0,
            solar_loss_kw=solar_loss,
            battery_loss_kw=batt_loss,
            ev_battery_loss_kw=ev_loss,
            standby_loss_kw=total_standby_kw,
            soc_kwh=batt_result.get("soc_kwh", 0.0),
            soc_pct=batt_result.get("soc_pct", 0.0),
            battery_eta=batt_result.get("eta", 0.0),
            ev_soc_kwh=ev_result.get("soc_kwh", 0.0),
            ev_soc_pct=ev_result.get("soc_pct", 0.0),
            generator_fuel_step_L=gen_result.get("fuel_step_L", 0.0),
            generator_running=gen_result.get("running", False),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _port_loss(battery_kw: float) -> float:
    p_abs_w = abs(battery_kw) * 1000.0
    if p_abs_w < 1e-3:
        return 0.0
    eta_b = eta("battery", p_abs_w)
    if battery_kw > 0:
        return battery_kw * (1.0 - eta_b)
    else:
        return abs(battery_kw) * (1.0 / eta_b - 1.0) if eta_b > 0 else 0.0


def _port_loss_ev(ev_kw: float) -> float:
    """Simplified fixed-η EV port loss (no lookup table needed)."""
    return abs(ev_kw) * (1.0 - EV_ETA)
