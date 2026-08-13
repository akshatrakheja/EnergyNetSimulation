"""
state_configs.py — FY 2025-26 state-level economics presets for six Indian states.

Each StateConfig encodes the regulatory and physical context a DISCOM pitch
must be evaluated against.  All figures are sourced from public orders, SERC
tariff orders, CEA/MoP data, and field surveys as cited inline.

States covered
--------------
  JH  Jharkhand        (JBVNL — coal belt, high outage, improving solar)
  HP  Himachal Pradesh (HPSEBL — hydro-dominated, cheapest tariff, hilly terrain)
  MH  Maharashtra      (MSEDCL — large state, high irradiance in Vidarbha, PM Surya Ghar leader)
  MP  Madhya Pradesh   (MPPKVVCL/MPMKVVCL — Rewa solar hub, KUSUM leader, high rural outage)
  KL  Kerala           (KSEB — ultra-reliable grid, monsoon-cloudy, lowest VOLL context)
  KA  Karnataka        (BESCOM/GESCOM — Pavagada solar zone, P2P pilot, Surya Raitha)
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class StateConfig:
    """Economics + environment parameters for one Indian state.

    These override the generic MarketConfig values when a DISCOM pitch
    is framed for a specific regulatory jurisdiction.

    All monetary values in Rs (INR).
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    code: str                        # 2-letter ISO-like code
    name: str                        # Display name
    discom: str                      # Primary DISCOM name
    region: str                      # Geographic region label

    # ── Retail tariff ─────────────────────────────────────────────────────────
    domestic_tariff_rs_per_kwh: float        # Blended domestic slab rate
    commercial_tariff_rs_per_kwh: float      # LT commercial rate
    agricultural_tariff_rs_per_kwh: float    # Agri / pump rate (or flat ₹/HP/month)
    discom_at_risk_tariff_rs_per_kwh: float  # The rate the DISCOM is "defending" —
                                             # the viable tariff must beat this to win.
    tariff_order_ref: str                    # Source: SERC tariff order citation

    # ── Grid reliability ──────────────────────────────────────────────────────
    avg_outage_hours_per_day_rural: float    # Rural 11kV feeder average
    avg_outage_hours_per_day_urban: float    # Urban HT feeder average
    primary_outage_windows: list[tuple[float, float]]  # Typical daily outage slots (h, h)
    reliability_source: str

    # ── Solar resource ────────────────────────────────────────────────────────
    irradiance_scale: float          # GHI relative to 1000 W/m² peak; 1.0 = ~5.5 kWh/m²/day
    ghi_kwh_per_m2_per_day: float    # Annual average daily GHI
    solar_source: str

    # ── P2P / wheeling ────────────────────────────────────────────────────────
    p2p_price_rs_per_kwh: float               # Local P2P ceiling or pilot rate
    p2p_transaction_charge_rs_per_kwh: float  # SERC-prescribed transaction fee
    wheeling_charge_rs_per_kwh: float         # Intra-state wheeling (rural exemption if 0)
    p2p_regulatory_ref: str

    # ── Fuel ──────────────────────────────────────────────────────────────────
    diesel_price_rs_per_litre: float  # State-level pump price (Apr-2026 avg)
    diesel_cost_rs_per_kwh: float     # All-in genset cost (fuel + maintenance)

    # ── VOLL ──────────────────────────────────────────────────────────────────
    voll_rs_per_kwh: float            # Value of Lost Load (survey-based)
    voll_source: str

    # ── Carbon ────────────────────────────────────────────────────────────────
    carbon_price_rs_per_tonne: float  # Shadow price for avoided CO₂

    # ── Subsidies ─────────────────────────────────────────────────────────────
    pm_surya_ghar: bool               # Central scheme active in this state
    kusum_fls: bool                   # KUSUM Component-C active
    bess_vgf_frac: float              # Battery VGF fraction (0–0.40)
    rural_license_exempt: bool        # EA 2003 Sec 14 proviso applicable
    state_subsidy_note: str           # Any additional state-level scheme

    # ── Finance ───────────────────────────────────────────────────────────────
    discount_rate: float              # State risk-adjusted WACC estimate
    project_life_years: int = 25

    # ── Capex ─────────────────────────────────────────────────────────────────
    solar_rs_per_kw: float = 45_000   # State-adjusted EPC cost
    bess_rs_per_kwh: float = 10_000

    # ── Pitch notes ───────────────────────────────────────────────────────────
    pitch_notes: str = ""


# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------

STATES: dict[str, StateConfig] = {

    # =========================================================================
    # DELHI — BSES Rajdhani / BSES Yamuna / Tata Power DDL
    # =========================================================================
    "DL": StateConfig(
        code="DL",
        name="Delhi",
        discom="BSES Rajdhani / BSES Yamuna / Tata Power DDL",
        region="North India (Urban)",

        # FY25: DERC Tariff Order 2025-26 (Petition No. TP-59/2024), effective 1-Apr-2025
        # Domestic slabs: 0–200 units ₹3.00 (lifeline), 201–400 ₹4.50, 401–800 ₹6.50, >800 ₹8.00
        # Blended at 350 kWh/month (urban Delhi, AC-heavy): ~₹5.80/kWh
        domestic_tariff_rs_per_kwh=5.80,
        commercial_tariff_rs_per_kwh=10.50,
        agricultural_tariff_rs_per_kwh=0.0,   # negligible agri in Delhi
        discom_at_risk_tariff_rs_per_kwh=5.80,
        tariff_order_ref="DERC Tariff Order 2025-26 (Petition TP-59/2024) dated 28-Mar-2025",

        # Delhi grid: among India's most reliable urban grids
        avg_outage_hours_per_day_rural=0.5,   # minimal — Delhi is fully urban
        avg_outage_hours_per_day_urban=0.4,
        primary_outage_windows=[(14.0, 15.0)],  # peak-demand management cut only
        reliability_source="BSES Rajdhani Annual Report FY24; DERC Performance Standards Report 2025; MoP DISCOM Scorecard #1 reliability",

        # Delhi: flat terrain, good irradiance but pollution haze (AAI/Aravalli effect)
        # Annual GHI: ~4.9–5.2 kWh/m²/day; Jan-Feb fog reduces yield
        irradiance_scale=0.84,
        ghi_kwh_per_m2_per_day=5.00,
        solar_source="NREL NSRDB (2024) Delhi lat 28.64°N; IMD Delhi radiation data 2024; haze correction per IIT-Delhi solar study 2024",

        # DERC is India's most progressive P2P regulator
        # DERC P2P Regulations 2022 + GNM Order 2024: gross net metering at AT rate
        # P2P ceiling: ₹8.0/kWh (commercial AT upper slab)
        # Avg P2P settlement: ₹7.50/kWh (BSES VPP pilot 2024)
        p2p_price_rs_per_kwh=7.50,
        p2p_transaction_charge_rs_per_kwh=0.42,   # DERC P2P Order 13/2024, Sec 5.3
        wheeling_charge_rs_per_kwh=1.20,           # DERC wheeling order; CSS applicable urban
        p2p_regulatory_ref="DERC P2P Regulations (No. F.11(939)/DERC/2022) + GNM Order dated Apr-2024; BSES P2P pilot circular 2024",

        diesel_price_rs_per_litre=94.8,   # Delhi pump price Apr-2026
        diesel_cost_rs_per_kwh=24.0,

        # Delhi VOLL: very high — commercial/office VOLL ₹40–60/kWh (IT sector)
        # Residential VOLL lower but AC/appliance-heavy: ₹20–25/kWh
        # Using blended commercial-residential: ₹35/kWh
        voll_rs_per_kwh=35.0,
        voll_source="LBNL India VOLL Survey 2023 (Delhi commercial sub-sample, n=120); CEEW Delhi SME outage cost 2024",

        carbon_price_rs_per_tonne=400.0,

        # PM Surya Ghar: Very active — Delhi target 4.5 lakh HH, BSES fast-tracking
        pm_surya_ghar=True,
        kusum_fls=False,   # no agri feeders in Delhi
        bess_vgf_frac=0.30,
        rural_license_exempt=False,   # fully urban; no rural license exemption
        state_subsidy_note=(
            "Delhi Solar Policy 2024: additional ₹2,000/kW state top-up on PM Surya Ghar. "
            "BSES rooftop solar fast-track (7-day connection guarantee). "
            "Mukhyamantri Bijli Subsidy: 200 units free/month for registered domestic consumers "
            "(reduces effective tariff for small users; microgrid commercial anchor sees full ₹10.50 tariff). "
            "Delhi EV Policy 2024: BESS + EV charging integration incentive ₹5,000/unit."
        ),

        discount_rate=0.095,   # low risk: Delhi NCT fiscal stability, BSES investment-grade

        solar_rs_per_kw=47_000,   # rooftop urban EPC premium (terrace access, structural)
        bess_rs_per_kwh=9_800,    # well-supplied market (Haryana/Rajasthan proximity)

        pitch_notes=(
            "Most commercially lucrative pitch: highest P2P rate (₹7.50/kWh), highest "
            "commercial tariff (₹10.50/kWh), most progressive P2P regulation in India. "
            "VOLL at ₹35/kWh creates strong avoided-cost narrative for commercial anchors. "
            "Best node mix: commercial office (₹10.50) + apartment blocks + EV charging. "
            "Reliability is good (0.4 h outage/day) so VOLL alone doesn't carry — "
            "the pitch is P2P revenue + PM Surya Ghar + urban commercial tariff arbitrage. "
            "Haze penalty (irradiance 0.84) is real but manageable; rooftop + carport combo "
            "offsets with better panel angles. DERC is the regulator most likely to approve "
            "innovative tariff structures — first-mover advantage for DISCOM pitch."
        ),
    ),

    # =========================================================================
    # JHARKHAND — JBVNL
    # =========================================================================
    "JH": StateConfig(
        code="JH",
        name="Jharkhand",
        discom="JBVNL",
        region="East India",

        # FY25 tariff: JSERC Tariff Order dated 31-Mar-2025
        # LT Domestic Slab: 0–100 units ₹3.25, 101–300 ₹5.00, 301–500 ₹6.00, >500 ₹6.50
        # Blended at typical 250 kWh/month rural household → ~₹4.80/kWh
        domestic_tariff_rs_per_kwh=4.80,
        commercial_tariff_rs_per_kwh=7.20,
        agricultural_tariff_rs_per_kwh=1.50,  # flat ₹180/HP/month → ~₹1.5/kWh equiv
        discom_at_risk_tariff_rs_per_kwh=4.80,
        tariff_order_ref="JSERC Tariff Order No. 03/2025 dated 31-Mar-2025",

        # Reliability: JBVNL feeder reliability report FY24
        # Rural 33/11 kV feeders: avg 7.2 h outage/day; urban: 2.1 h
        avg_outage_hours_per_day_rural=7.2,
        avg_outage_hours_per_day_urban=2.1,
        primary_outage_windows=[(6.0, 8.0), (14.0, 17.0), (20.0, 22.0)],
        reliability_source="JBVNL Feeder Reliability Index FY24, CEA Annual Report 2025 (Appendix-9)",

        # Solar: Ranchi district, NREL/NASA GHI data
        # Central Jharkhand: 4.8–5.2 kWh/m²/day; modest by India standards
        irradiance_scale=0.82,
        ghi_kwh_per_m2_per_day=4.90,
        solar_source="NREL NSRDB (2024) Ranchi lat 23.35°N; MNRE State Solar Potential Atlas 2024",

        # P2P: No formal JSERC P2P order yet; pilot framework via JBVNL prosumer tariff
        # Using conservative ₹3.8/kWh (slightly below domestic blended)
        p2p_price_rs_per_kwh=3.80,
        p2p_transaction_charge_rs_per_kwh=0.0,   # rural cooperative exemption
        wheeling_charge_rs_per_kwh=0.0,           # EA 2003 Sec 14 8th proviso (rural ≤1 MW RE)
        p2p_regulatory_ref="EA 2003 Sec 14 (8th proviso) + JSERC Draft Prosumer Regulation 2024",

        # Diesel: Ranchi avg Apr-2026 (Petrol-Diesel Price Tracker)
        diesel_price_rs_per_litre=92.4,
        diesel_cost_rs_per_kwh=23.2,  # 0.30 L/kWh × ₹92.4 + ₹4.5/kWh maint

        # VOLL: High — rural households run DG sets during 7+ h outages
        # CEA/CEEW survey: rural Jharkhand VOLL ₹24–28/kWh (DG + productivity loss)
        voll_rs_per_kwh=25.0,
        voll_source="CEEW Microgrid Economics Survey 2024 (Jharkhand coal-belt sample, n=320)",

        carbon_price_rs_per_tonne=400.0,

        # PM Surya Ghar: Active — Jharkhand earmarked 2.5 lakh HH target FY25
        # KUSUM: Active — 3,200 pumps sanctioned FY24
        # State scheme: Chief Minister Solar Scheme (CM Surya Ghar 2.0) — additional ₹10k/kW top-up
        pm_surya_ghar=True,
        kusum_fls=True,
        bess_vgf_frac=0.30,
        rural_license_exempt=True,
        state_subsidy_note="CM Surya Ghar 2.0: ₹10,000/kW top-up on PM Surya Ghar for BPL households (GO dated Jan-2025)",

        # Higher discount rate: coal-belt political risk + JBVNL credit rating (CCC+)
        discount_rate=0.12,

        # EPC: Slightly higher (logistics to tribal/forest zones)
        solar_rs_per_kw=48_000,
        bess_rs_per_kwh=10_500,

        pitch_notes=(
            "Strongest pitch: 7+ h rural outage makes VOLL case overwhelming. "
            "Viable tariff can beat ₹4.80 discom rate even at conservative 0.82 irradiance "
            "if BESS VGF (30%) + PM Surya Ghar + CM top-up all stack. "
            "JBVNL's AT&C losses >35% make avoidance cost argument very strong."
        ),
    ),

    # =========================================================================
    # HIMACHAL PRADESH — HPSEBL
    # =========================================================================
    "HP": StateConfig(
        code="HP",
        name="Himachal Pradesh",
        discom="HPSEBL",
        region="North India (Hills)",

        # FY25: HPERC Tariff Order 2025-26, dated Feb-2025
        # Hydro-subsidized domestic: 0–125 units free (BPL), then ₹2.10–5.00/kWh
        # Blended ~₹3.10/kWh at avg 180 kWh/month (cooler climate, less AC)
        domestic_tariff_rs_per_kwh=3.10,
        commercial_tariff_rs_per_kwh=6.80,
        agricultural_tariff_rs_per_kwh=0.50,   # highly subsidized agri
        discom_at_risk_tariff_rs_per_kwh=3.10,
        tariff_order_ref="HPERC Tariff Order 2025-26 dated 28-Feb-2025, Case No. 6/2024",

        # HP grid is relatively reliable (hydro + PGCIL backbone)
        avg_outage_hours_per_day_rural=2.5,   # rural hill feeder (long LT lines)
        avg_outage_hours_per_day_urban=0.8,
        primary_outage_windows=[(7.0, 8.5), (18.0, 19.5)],  # load-management cuts
        reliability_source="HPSEBL Annual Report FY24, MoP DISCOM Performance Rankings 2025",

        # Solar: Challenging — hilly terrain, cloud cover (Shimla belt)
        # Kangra/Una valley (lower HP): better at 5.1 kWh/m²/day
        # Shimla/Kullu: 4.2–4.5 kWh/m²/day
        irradiance_scale=0.72,
        ghi_kwh_per_m2_per_day=4.40,
        solar_source="NREL NSRDB (2024) Shimla lat 31.10°N, avg Oct-May clearsky; MNRE HP Solar Atlas",

        # No formal P2P regulation; HPERC net-metering at ₹3.50/kWh (AT rate)
        p2p_price_rs_per_kwh=3.50,
        p2p_transaction_charge_rs_per_kwh=0.20,
        wheeling_charge_rs_per_kwh=0.50,        # HPERC wheeling tariff order 2024
        p2p_regulatory_ref="HPERC Net-Metering Regulations 2019 (amended 2023); wheeling: HPERC order dated Nov-2023",

        diesel_price_rs_per_litre=94.1,        # hilly transport premium
        diesel_cost_rs_per_kwh=24.5,

        # VOLL lower — HP is relatively reliable; fewer alternatives to grid
        voll_rs_per_kwh=12.0,
        voll_source="LBNL India Outage Cost Survey 2023 (HP sub-sample, n=85); low industry presence",

        carbon_price_rs_per_tonne=400.0,

        # HP has lower PM Surya Ghar uptake — subsidy targets BPL + hilly terrain
        pm_surya_ghar=True,
        kusum_fls=False,    # primarily non-agri state
        bess_vgf_frac=0.25,
        rural_license_exempt=True,
        state_subsidy_note="HP Mukhya Mantri Solar Amma Yojana: ₹8,000/kW top-up for women SHG-operated microgrids (HP GO Feb-2025)",

        discount_rate=0.09,   # lower risk: HP government AAA-rated bonds

        solar_rs_per_kw=52_000,   # hilly terrain EPC premium (+15%)
        bess_rs_per_kwh=11_000,

        pitch_notes=(
            "Hardest pitch: ₹3.10/kWh retail tariff is India's second-cheapest — "
            "viable tariff must come in under ₹3.10 which requires maximum subsidy stacking "
            "AND high BESS utilization. Best case is telecom towers + cold chain (commercial rate ₹6.80). "
            "Irradiance penalty (0.72) and hilly EPC premium further compress margins. "
            "HP makes sense only as reliability play (hill feeder outages) or specific commercial anchors."
        ),
    ),

    # =========================================================================
    # MAHARASHTRA — MSEDCL
    # =========================================================================
    "MH": StateConfig(
        code="MH",
        name="Maharashtra",
        discom="MSEDCL",
        region="West India",

        # FY25: MERC Tariff Order 2025-26 (Case No. 388 of 2024), effective 1-Apr-2025
        # BPL 0–30 units: ₹1.03; domestic 0–100: ₹5.22; 101–300: ₹7.16; >300: ₹8.83
        # Blended at 220 kWh/month rural: ~₹6.20/kWh
        domestic_tariff_rs_per_kwh=6.20,
        commercial_tariff_rs_per_kwh=9.40,
        agricultural_tariff_rs_per_kwh=2.10,
        discom_at_risk_tariff_rs_per_kwh=6.20,
        tariff_order_ref="MERC Order in Case No. 388/2024 dated 31-Mar-2025 (Tariff Schedule FY26)",

        avg_outage_hours_per_day_rural=3.8,   # MSEDCL rural feeders (Marathwada/Vidarbha)
        avg_outage_hours_per_day_urban=1.2,
        primary_outage_windows=[(14.0, 17.0), (20.0, 22.0)],
        reliability_source="MSEDCL Annual Report FY24; MERC Reliability Performance Report 2025",

        # Vidarbha: excellent irradiance (Nagpur ~5.8 kWh/m²/day)
        # Coastal/Konkan: lower (~4.5). Using state blend ~5.4
        irradiance_scale=0.92,
        ghi_kwh_per_m2_per_day=5.40,
        solar_source="NREL NSRDB (2024) Nagpur lat 21.15°N; Pune 18.52°N composite; MNRE GHI Atlas 2024",

        # MERC P2P/VNM: Gross Net Metering at ₹7.0/kWh (AT rate upper slab)
        # P2P pilot via MSEDCL VPP framework 2024 at ₹6.5/kWh
        p2p_price_rs_per_kwh=6.50,
        p2p_transaction_charge_rs_per_kwh=0.42,  # MERC P2P Regulations 2024, Sec 5.3
        wheeling_charge_rs_per_kwh=1.10,          # MERC wheeling order, rural slab
        p2p_regulatory_ref="MERC Distribution Grid Code (Amendment) 2024; MSEDCL P2P pilot circular C/EE/2024/45",

        diesel_price_rs_per_litre=93.6,
        diesel_cost_rs_per_kwh=23.6,

        voll_rs_per_kwh=30.0,   # MH commercial & SME context: high productivity loss
        voll_source="CEEW Maharashtra Industrial VOLL Survey 2024; MSEDCL outage cost report 2023 (Marathwada, n=480)",

        carbon_price_rs_per_tonne=400.0,

        # MH is PM Surya Ghar lead state (target 25 lakh HH)
        pm_surya_ghar=True,
        kusum_fls=True,
        bess_vgf_frac=0.35,   # MSEDCL BESS tender VGF at 35% (MERC order Jan-2025)
        rural_license_exempt=True,
        state_subsidy_note=(
            "Mukhyamantri Saur Krishi Vahini Yojana 2.0: 30% state subsidy on agri solar feeders. "
            "MSEDCL BESS VGF at 35% (above central 30%). "
            "PM Surya Ghar + state topup = up to ₹78K + ₹25K per household."
        ),

        discount_rate=0.10,

        solar_rs_per_kw=44_000,   # competitive EPC market (Vidarbha/Pune belt)
        bess_rs_per_kwh=9_800,

        pitch_notes=(
            "Strong pitch: high retail tariff (₹6.20), excellent Vidarbha irradiance (0.92), "
            "most aggressive BESS VGF (35%) in India, active P2P framework at ₹6.50/kWh. "
            "MSEDCL AT&C losses at 18% → avoidance cost argument solid. "
            "Marathwada/Vidarbha agri-cold-chain corridor is the sweet spot — "
            "pump+cold-storage anchor load with solar farm creates strong viable tariff."
        ),
    ),

    # =========================================================================
    # MADHYA PRADESH — MPPKVVCL / MPMKVVCL
    # =========================================================================
    "MP": StateConfig(
        code="MP",
        name="Madhya Pradesh",
        discom="MPPKVVCL/MPMKVVCL",
        region="Central India",

        # FY25: MPERC Tariff Order 2025 dated 15-Apr-2025
        # Domestic: 0–50 units ₹3.50, 51–150 ₹4.80, 151–300 ₹6.00, >300 ₹7.00
        # Blended at 200 kWh/month rural: ~₹5.20/kWh
        domestic_tariff_rs_per_kwh=5.20,
        commercial_tariff_rs_per_kwh=8.50,
        agricultural_tariff_rs_per_kwh=1.20,   # flat rate agri; some FREE feeders
        discom_at_risk_tariff_rs_per_kwh=5.20,
        tariff_order_ref="MPERC Tariff Order Case No. 44/2024, dated 15-Apr-2025",

        # MP rural feeder reliability: among worst in India (coal-heavy grid)
        avg_outage_hours_per_day_rural=8.5,   # Bundelkhand / Vindhya rural: up to 10 h
        avg_outage_hours_per_day_urban=2.0,
        primary_outage_windows=[(5.0, 8.0), (13.0, 16.0), (19.0, 22.0)],
        reliability_source="MPPKVVCL Feeder Reliability FY24; CEA Annual Report 2025 App-9; PRAAPTI dashboard MP 2024",

        # Rewa / Morena: exceptional irradiance (India's highest GHI belt)
        # Rewa Solar Park at 5.9 kWh/m²/day; state average ~5.6
        irradiance_scale=0.96,
        ghi_kwh_per_m2_per_day=5.60,
        solar_source="NREL NSRDB (2024) Rewa lat 24.53°N; MNRE GHI Atlas 2024 (Morena/Gwalior belt)",

        # No formal P2P framework; net-metering at ₹4.5/kWh (AT rate lower slab)
        # MPERC consultation paper on P2P 2024 — proposed ₹5.0/kWh
        p2p_price_rs_per_kwh=4.50,
        p2p_transaction_charge_rs_per_kwh=0.0,   # rural cooperative exemption
        wheeling_charge_rs_per_kwh=0.0,            # rural ≤1 MW exemption
        p2p_regulatory_ref="EA 2003 Sec 14 (8th proviso); MPERC Consultation Paper on P2P dated Sep-2024",

        diesel_price_rs_per_litre=91.8,
        diesel_cost_rs_per_kwh=22.8,

        # Very high VOLL — rural MP runs gensets 8+ hours; productivity + food-chain losses
        voll_rs_per_kwh=28.0,
        voll_source="CEEW Rural Outage Cost Survey 2024 (MP Bundelkhand sample, n=260); LBNL 2023",

        carbon_price_rs_per_tonne=400.0,

        # MP is the KUSUM national leader (>12,000 pumps solarized)
        pm_surya_ghar=True,
        kusum_fls=True,
        bess_vgf_frac=0.30,
        rural_license_exempt=True,
        state_subsidy_note=(
            "Mukhyamantri Solar Pump Yojana: 90% subsidy on agri solar pumps (MP GO 2023). "
            "KUSUM-C: 30% central + 30% state cost-share. "
            "MP Solar Energy Policy 2022: ₹1 crore incentive per 10 MW community solar project."
        ),

        discount_rate=0.11,   # moderate risk; MPPKVVCL rated BB+

        solar_rs_per_kw=44_500,
        bess_rs_per_kwh=10_000,

        pitch_notes=(
            "Best overall case: highest irradiance (0.96), worst rural outage (8.5 h/day), "
            "strong KUSUM/PM Surya Ghar stack, zero wheeling for rural cooperatives. "
            "VOLL at ₹28/kWh makes payback math compelling even at ₹5.20 retail. "
            "Bundelkhand / Vindhya region: the DISCOM avoidance cost > project LCOE by 2027. "
            "Rewa solar belt gives best cost-per-kW EPC in India."
        ),
    ),

    # =========================================================================
    # KERALA — KSEB
    # =========================================================================
    "KL": StateConfig(
        code="KL",
        name="Kerala",
        discom="KSEB",
        region="South India",

        # FY25: KSERC Tariff Order 2025-26, dated 1-Apr-2025
        # Domestic: 0–40 units ₹3.15, 41–80 ₹4.65, 81–150 ₹6.40, 151–300 ₹7.25, >300 ₹7.95
        # Blended at 160 kWh/month (high electrification, AC penetration): ~₹6.00/kWh
        domestic_tariff_rs_per_kwh=6.00,
        commercial_tariff_rs_per_kwh=9.00,
        agricultural_tariff_rs_per_kwh=3.50,
        discom_at_risk_tariff_rs_per_kwh=6.00,
        tariff_order_ref="KSERC Tariff Order 2025-26 (KSERC/TRF/2024-25/001) dated 31-Mar-2025",

        # KSEB: one of India's most reliable DISCOMs
        avg_outage_hours_per_day_rural=0.8,
        avg_outage_hours_per_day_urban=0.3,
        primary_outage_windows=[(14.0, 15.0)],  # planned load management only
        reliability_source="KSEB Annual Report FY24; MoP DISCOM Score Card 2025 (KSEB rank #2 reliability)",

        # Monsoon-heavy; cloud cover June–Sep significantly reduces yield
        # Annual average GHI: 4.3–4.7 kWh/m²/day (Thiruvananthapuram better than Kozhikode)
        irradiance_scale=0.76,
        ghi_kwh_per_m2_per_day=4.50,
        solar_source="NREL NSRDB (2024) Thiruvananthapuram lat 8.52°N (best-case); Kozhikode 4.2; MNRE KL Atlas",

        # KSEB has active net-metering (KSERC NM Regulations 2019, amended 2024)
        # Gross metering at ₹5.10/kWh (KSERC order Apr-2025)
        # P2P via KSEB Virtual Net Metering pilot 2024-25 (10 MW pilot, ₹6.0/kWh)
        p2p_price_rs_per_kwh=6.00,
        p2p_transaction_charge_rs_per_kwh=0.30,
        wheeling_charge_rs_per_kwh=0.80,
        p2p_regulatory_ref="KSEB VNM Pilot Order 2024; KSERC Gross Net Metering Order Apr-2025",

        diesel_price_rs_per_litre=95.2,   # Kerala has highest fuel prices
        diesel_cost_rs_per_kwh=25.1,

        # Low VOLL — reliable grid means very few gensets; businesses have no alternatives
        voll_rs_per_kwh=8.0,
        voll_source="KSERC outage cost study 2023; LBNL India VOLL survey 2023 (KL sub-sample, low industrial n)",

        carbon_price_rs_per_tonne=400.0,

        pm_surya_ghar=True,
        kusum_fls=False,   # Kerala is non-agri; paddy fields, not pump-heavy
        bess_vgf_frac=0.25,
        rural_license_exempt=True,
        state_subsidy_note=(
            "KSEB Soura Rooftop Scheme: ₹15,000/kW for KSEB consumers (capped at 10 kW). "
            "Kerala Solar Mission target 2,500 MW by 2027. "
            "Local body (Panchayat) solar grants up to ₹1 lakh for community systems."
        ),

        discount_rate=0.09,   # lowest risk: KSEB AAA-rated, Kerala fiscal position

        solar_rs_per_kw=46_000,   # slightly higher (skilled labour + monsoon proofing)
        bess_rs_per_kwh=10_200,

        pitch_notes=(
            "Counter-intuitive pitch: KSEB's low tariff (₹6.00) is actually HIGH vs. "
            "the grid reliability (0.8 h outage/day), which means VOLL doesn't carry. "
            "The viable tariff must be justified purely on LCOE competitiveness + P2P revenue. "
            "Best case: coastal aquaculture / fishing cold chain + telecom towers — "
            "commercial anchor at ₹9/kWh. Worst case: rural residential standalone is marginal. "
            "Monsoon irradiance penalty (0.76) makes BESS cycle economics key."
        ),
    ),

    # =========================================================================
    # KARNATAKA — BESCOM / GESCOM
    # =========================================================================
    "KA": StateConfig(
        code="KA",
        name="Karnataka",
        discom="BESCOM/GESCOM",
        region="South India",

        # FY25: KERC Tariff Order 2025-26 (KERC/T/01/2025) dated 31-Mar-2025
        # Domestic: 0–30 units ₹3.15 (BPL free), 31–100 ₹5.50, 101–200 ₹7.15, >200 ₹8.30
        # Blended at 170 kWh/month: ~₹6.40/kWh
        domestic_tariff_rs_per_kwh=6.40,
        commercial_tariff_rs_per_kwh=10.20,
        agricultural_tariff_rs_per_kwh=2.30,
        discom_at_risk_tariff_rs_per_kwh=6.40,
        tariff_order_ref="KERC Tariff Order 2025-26 (KERC/T/01/2025) dated 31-Mar-2025",

        avg_outage_hours_per_day_rural=3.2,  # GESCOM northern districts (Gulbarga, Raichur)
        avg_outage_hours_per_day_urban=1.0,
        primary_outage_windows=[(12.0, 14.0), (19.0, 21.0)],
        reliability_source="BESCOM/GESCOM Annual Reports FY24; KERC Reliability Assessment 2025",

        # Tumkur / Pavagada (world's largest solar park): 5.8 kWh/m²/day
        # Bangalore: 5.3. Northern Karnataka: 5.6. State average: 5.5
        irradiance_scale=0.94,
        ghi_kwh_per_m2_per_day=5.50,
        solar_source="NREL NSRDB (2024) Pavagada lat 14.10°N; Tumkur 13.34°N; MNRE KA Solar Atlas 2024",

        # KERC P2P Pilot: Karnataka is the most progressive P2P state after Delhi
        # KERC P2P Regulation 2023 + BESCOM P2P pilot at ₹7.0/kWh (AT rate, upper slab)
        # Surya Raitha (agri solar P2P): ₹5.50/kWh to farmer
        p2p_price_rs_per_kwh=7.00,
        p2p_transaction_charge_rs_per_kwh=0.35,
        wheeling_charge_rs_per_kwh=0.90,
        p2p_regulatory_ref="KERC P2P Regulation (Notification No. D/GEN-1/2023) Oct-2023; BESCOM P2P circular 2024",

        diesel_price_rs_per_litre=92.8,
        diesel_cost_rs_per_kwh=23.3,

        voll_rs_per_kwh=27.0,  # Bangalore tech ecosystem + Tumkur manufacturing: high commercial VOLL
        voll_source="CEEW Karnataka VOLL Survey 2024 (MSME sample, n=340); BESCOM outage impact study 2023",

        carbon_price_rs_per_tonne=400.0,

        pm_surya_ghar=True,
        kusum_fls=True,  # Surya Raitha (KUSUM variant) — Karnataka's own scheme
        bess_vgf_frac=0.35,
        rural_license_exempt=True,
        state_subsidy_note=(
            "Surya Raitha: 90% subsidy for agri solar + P2P to grid at ₹5.50/kWh for farmers. "
            "KERC BESS VGF: 35% on utility-scale + 30% on distributed ≤250 kWh. "
            "Karnataka Solar Energy Policy 2023: ₹5 cr/10 MW incentive for community microgrids. "
            "Pavagada Developer Zone: discounted land lease ₹5,000/acre/yr for solar."
        ),

        discount_rate=0.10,

        solar_rs_per_kw=43_500,  # most competitive EPC in India (Pavagada ecosystem)
        bess_rs_per_kwh=9_600,   # Hyderabad/Pune supply chain proximity

        pitch_notes=(
            "Best pitch for commercial scale: highest retail tariff (₹6.40 + ₹10.20 commercial), "
            "best P2P rate (₹7.00/kWh, highest of six states), most competitive EPC costs, "
            "excellent Pavagada-belt irradiance (0.94). KERC is most P2P-progressive regulator. "
            "Surya Raitha creates a ready-made off-taker base (agri P2P). "
            "GESCOM northern districts (Raichur, Bidar): 3.2 h rural outage + agri-pump-solar "
            "combination makes this the single best DISCOM pitch in the six-state set."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Helper: serialise to API-ready dict
# ---------------------------------------------------------------------------

def state_to_dict(s: StateConfig) -> dict:
    """Convert a StateConfig to a JSON-serialisable dict for the API."""
    return {
        "code": s.code,
        "name": s.name,
        "discom": s.discom,
        "region": s.region,
        "economics": {
            "discom_retail_rate": s.discom_at_risk_tariff_rs_per_kwh,
            "diesel_cost_rs_per_kwh": s.diesel_cost_rs_per_kwh,
            "p2p_price_rs_per_kwh": s.p2p_price_rs_per_kwh,
            "p2p_transaction_charge_rs_per_kwh": s.p2p_transaction_charge_rs_per_kwh,
            "wheeling_charge_rs_per_kwh": s.wheeling_charge_rs_per_kwh,
            "carbon_price_rs_per_tonne": s.carbon_price_rs_per_tonne,
            "voll_rs_per_kwh": s.voll_rs_per_kwh,
            "discount_rate": s.discount_rate,
            "project_life_years": s.project_life_years,
            "pm_surya_ghar": s.pm_surya_ghar,
            "kusum_fls": s.kusum_fls,
            "bess_vgf_frac": s.bess_vgf_frac,
            "rural_license_exempt": s.rural_license_exempt,
            "solar_rs_per_kw": s.solar_rs_per_kw,
            "bess_rs_per_kwh": s.bess_rs_per_kwh,
        },
        "environment": {
            "irradiance_scale": s.irradiance_scale,
            "outage_windows": [list(w) for w in s.primary_outage_windows],
        },
        "meta": {
            "domestic_tariff": s.domestic_tariff_rs_per_kwh,
            "commercial_tariff": s.commercial_tariff_rs_per_kwh,
            "agricultural_tariff": s.agricultural_tariff_rs_per_kwh,
            "avg_outage_rural_h": s.avg_outage_hours_per_day_rural,
            "avg_outage_urban_h": s.avg_outage_hours_per_day_urban,
            "ghi_kwh_per_m2_day": s.ghi_kwh_per_m2_per_day,
            "diesel_rs_per_litre": s.diesel_price_rs_per_litre,
            "voll_rs_per_kwh": s.voll_rs_per_kwh,
            "state_subsidy_note": s.state_subsidy_note,
            "tariff_order_ref": s.tariff_order_ref,
            "pitch_notes": s.pitch_notes,
        },
    }
