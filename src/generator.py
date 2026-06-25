"""
generator.py — Dispatchable generator model (diesel genset / biogas).

A Generator is a controllable power source, unlike PV which is non-dispatchable.
It has:
  - A minimum load fraction (generators run inefficiently below ~30% load)
  - A maximum rated power
  - A fuel consumption model (liters per kWh or liters per hour at given load)
  - An optional ramp rate limit

The dispatch policy sets `generator_kw` in NodeSetpoints; the Router converts
this to a net bus contribution and records fuel consumption.

Typical use-cases:
  - Diesel genset backup during islanding / grid outages
  - Biogas generator (base-load, always-on up to capacity)
  - Auto-rickshaw charging station with small genset for peak-shaving

Fuel model (diesel):
  Based on standard diesel genset fuel curves (BEE / MNRE genset efficiency
  guidelines, 2021).  Specific fuel consumption (SFC) is approximately:
    SFC(kW) = a/kW + b  (liters per kWh)
  where a ≈ 0.08, b ≈ 0.22 for a well-maintained 10–30 kW genset.
  At 75% load, SFC ≈ 0.30 L/kWh (consistent with MNRE data: 0.28–0.32 L/kWh).
  At 25% load (light loading), SFC rises to ≈0.55 L/kWh — hence min_load_frac.

Reference:
  BEE / MNRE, "Efficient Operation of Diesel Generating Sets", 2021.
  CPHEEO, "Manual on Electro-Mechanical Works", sec. 4.2 (fuel consumption curves).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Default diesel SFC parameters (BEE 2021, medium genset 10–30 kW)
_DIESEL_SFC_A: float = 0.08   # L/kWh, no-load overhead contribution
_DIESEL_SFC_B: float = 0.22   # L/kWh, variable part at full load


@dataclass
class Generator:
    """Dispatchable generator (diesel, biogas, or generic).

    Parameters
    ----------
    p_rated_kw : float
        Rated (maximum) output power in kW.
    min_load_frac : float
        Minimum stable loading fraction (0–1).  Below this, the generator
        should not run; dispatch policy must respect this or accept inefficiency.
    fuel_type : str
        'diesel' | 'biogas' | 'grid_backup'.
        'biogas' is modelled as zero marginal fuel cost but fixed capacity.
        'grid_backup' is an alias for diesel with higher SFC.
    sfc_a : float
        Specific fuel consumption offset (L/kWh) for no-load losses.
    sfc_b : float
        Specific fuel consumption slope (L/kWh) at rated load.
    ramp_rate_kw_per_min : float
        Max ramp rate; 0 = unconstrained (default, suitable for most simulations).
    running : bool
        Whether the generator is currently running (used for ramp logic).
    fuel_consumed_L : float
        Cumulative fuel consumed this session (L).  Reset externally if needed.
    """
    p_rated_kw: float
    min_load_frac: float = 0.25
    fuel_type: str = "diesel"
    sfc_a: float = _DIESEL_SFC_A
    sfc_b: float = _DIESEL_SFC_B
    ramp_rate_kw_per_min: float = 0.0   # 0 = unconstrained
    running: bool = False
    fuel_consumed_L: float = field(default=0.0, repr=False)

    @property
    def p_min_kw(self) -> float:
        return self.p_rated_kw * self.min_load_frac

    def max_output_kw(self) -> float:
        return self.p_rated_kw

    def step(self, requested_kw: float, dt_h: float) -> dict:
        """Advance one timestep.

        Parameters
        ----------
        requested_kw : float
            Desired output (kW), set by dispatch policy.  Clamped to [0, p_rated].
        dt_h : float
            Timestep duration in hours.

        Returns
        -------
        dict with:
          actual_kw     : float  — clamped actual output
          fuel_step_L   : float  — fuel used this timestep (L)
          sfc_L_per_kwh : float  — specific fuel consumption this step
          running       : bool
        """
        requested_kw = max(0.0, min(requested_kw, self.p_rated_kw))

        # Below minimum load: either shut down or hold at min
        if requested_kw < self.p_min_kw:
            if requested_kw < 1e-6:
                actual_kw = 0.0
                self.running = False
            else:
                actual_kw = self.p_min_kw  # hold at minimum
                self.running = True
        else:
            actual_kw = requested_kw
            self.running = True

        # Fuel consumption
        if self.running and actual_kw > 1e-6:
            if self.fuel_type == "biogas":
                sfc = 0.0   # biogas: zero marginal fuel cost
            else:
                sfc = self.sfc_a / actual_kw + self.sfc_b
            fuel_step = sfc * actual_kw * dt_h
        else:
            sfc = 0.0
            fuel_step = 0.0

        self.fuel_consumed_L += fuel_step

        return {
            "actual_kw": actual_kw,
            "fuel_step_L": fuel_step,
            "sfc_L_per_kwh": sfc,
            "running": self.running,
        }

    def reset_fuel_counter(self) -> None:
        self.fuel_consumed_L = 0.0
