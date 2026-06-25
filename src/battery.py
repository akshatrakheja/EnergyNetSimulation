"""
battery.py — Battery state-of-charge model.

Tracks energy (kWh), not just power. Uses the Phase 1 battery-port η(P)
curve for both charge and discharge efficiency.

Sign convention (consistent with router.py):
  p_port_kw > 0 → charging  (power flows *into* the battery port)
  p_port_kw < 0 → discharging (power flows *out of* the battery port)

SoC accounting:
  Charging:    soc += p_port_kw * η_b(p) * dt_h          (port → cell)
  Discharging: soc -= |p_port_kw| / η_b(|p|) * dt_h      (cell → port)

Round-trip efficiency ≈ η_b_charge × η_b_discharge ≈ 0.975² ≈ 0.95.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ports import eta, P_MAX_W

P_MAX_KW = P_MAX_W["battery"] / 1000.0   # 5 kW


@dataclass
class Battery:
    """Single battery attached to one router's battery port.

    Parameters
    ----------
    capacity_kwh : float
        Usable nameplate capacity.
    soc_init_kwh : float | None
        Initial SoC in kWh. Defaults to 50 % of capacity.
    soc_min_frac : float
        Lower SoC limit as fraction of capacity (default 0.10).
    soc_max_frac : float
        Upper SoC limit as fraction of capacity (default 0.95).
    p_max_kw : float
        Port power limit (default 5 kW from Phase 1).
    p_standby_w : float
        Standby draw when port is energised; exposed for sensitivity studies.
        The Phase 1 value (3.9 W) is an optimistic lower bound.
    """

    capacity_kwh: float
    soc_init_kwh: float | None = None
    soc_min_frac: float = 0.10
    soc_max_frac: float = 0.95
    p_max_kw: float = P_MAX_KW
    p_standby_w: float = 3.9

    # state (initialised in __post_init__)
    soc_kwh: float = field(init=False)
    half_cycles: float = field(init=False, default=0.0)
    _prev_direction: int = field(init=False, default=0)   # +1 charge, -1 discharge

    def __post_init__(self) -> None:
        if self.soc_init_kwh is None:
            self.soc_kwh = 0.5 * self.capacity_kwh
        else:
            self.soc_kwh = float(self.soc_init_kwh)
        self.soc_kwh = self._clamp_soc(self.soc_kwh)
        self.half_cycles = 0.0
        self._prev_direction = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def soc_min_kwh(self) -> float:
        return self.soc_min_frac * self.capacity_kwh

    @property
    def soc_max_kwh(self) -> float:
        return self.soc_max_frac * self.capacity_kwh

    @property
    def soc_pct(self) -> float:
        return 100.0 * self.soc_kwh / self.capacity_kwh

    @property
    def headroom_kwh(self) -> float:
        """Energy that can still be stored this step."""
        return max(0.0, self.soc_max_kwh - self.soc_kwh)

    @property
    def available_kwh(self) -> float:
        """Energy that can still be drawn this step."""
        return max(0.0, self.soc_kwh - self.soc_min_kwh)

    # ------------------------------------------------------------------
    # Power-limit helpers (used by dispatch layer)
    # ------------------------------------------------------------------

    def max_charge_kw(self) -> float:
        """Maximum charge power the port can accept right now (kW)."""
        return min(self.p_max_kw, self.headroom_kwh / 1.0)  # dt=1 h upper bound

    def max_discharge_kw(self) -> float:
        """Maximum discharge power the port can deliver right now (kW)."""
        return min(self.p_max_kw, self.available_kwh / 1.0)

    # ------------------------------------------------------------------
    # Step update
    # ------------------------------------------------------------------

    def step(self, p_port_kw: float, dt_h: float) -> dict:
        """Advance SoC by one timestep.

        Parameters
        ----------
        p_port_kw : float
            Power at the battery *port* (kW). Positive = charging.
        dt_h : float
            Timestep duration in hours.

        Returns
        -------
        dict with keys:
            p_port_kw   : actual (clamped) port power
            p_cell_kw   : net power delivered to/from cell (after η)
            eta         : port efficiency used
            loss_kw     : conversion loss (kW)
            soc_kwh     : SoC after this step (kWh)
            soc_pct     : SoC % after this step
        """
        p_port_kw = self._clamp_port(p_port_kw)
        p_abs_w = abs(p_port_kw) * 1000.0

        if p_port_kw > 0:
            # Charging: port power × η enters the cell
            e = eta("battery", p_abs_w)
            p_cell_kw = p_port_kw * e
            loss_kw = p_port_kw * (1.0 - e)
        elif p_port_kw < 0:
            # Discharging: cell delivers |p_port| / η
            e = eta("battery", p_abs_w)
            p_cell_kw = p_port_kw / e if e > 0 else 0.0
            loss_kw = abs(p_port_kw) * (1.0 / e - 1.0) if e > 0 else 0.0
        else:
            e = 0.0
            p_cell_kw = 0.0
            loss_kw = 0.0

        delta_kwh = p_cell_kw * dt_h
        new_soc = self._clamp_soc(self.soc_kwh + delta_kwh)

        # Recalculate actual p_cell if SoC hit a bound
        if abs(new_soc - (self.soc_kwh + delta_kwh)) > 1e-9:
            actual_delta = new_soc - self.soc_kwh
            p_cell_kw = actual_delta / dt_h if dt_h > 0 else 0.0
            # Recalculate loss based on clamped power
            if p_port_kw > 0 and e > 0:
                p_port_kw = p_cell_kw / e
                loss_kw = p_port_kw * (1.0 - e)
            elif p_port_kw < 0 and e > 0:
                p_port_kw = p_cell_kw * e
                loss_kw = abs(p_port_kw) * (1.0 / e - 1.0)

        self._track_cycles(p_port_kw)
        self.soc_kwh = new_soc

        return {
            "p_port_kw": p_port_kw,
            "p_cell_kw": p_cell_kw,
            "eta": e,
            "loss_kw": loss_kw,
            "soc_kwh": self.soc_kwh,
            "soc_pct": self.soc_pct,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clamp_soc(self, soc: float) -> float:
        return max(self.soc_min_kwh, min(self.soc_max_kwh, soc))

    def _clamp_port(self, p_kw: float) -> float:
        return max(-self.p_max_kw, min(self.p_max_kw, p_kw))

    def _track_cycles(self, p_kw: float) -> None:
        """Half-cycle counting: each direction reversal = +0.5 cycles."""
        if p_kw > 0:
            d = 1
        elif p_kw < 0:
            d = -1
        else:
            return
        if self._prev_direction != 0 and d != self._prev_direction:
            self.half_cycles += 1.0
        self._prev_direction = d

    @property
    def full_cycles(self) -> float:
        return self.half_cycles / 2.0
