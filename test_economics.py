"""
Simple test to verify economics layer calculations without running full simulations.
"""

from src.tariffs import TARIFFS, energy_charge, monthly_fixed_and_demand
from src.subsidies import SubsidyStack, apply_subsidies
from src.economics import crf, lcoe, CapexConfig, FinanceConfig
from src.markets import MARKETS

print("=" * 80)
print("ECONOMICS LAYER TEST")
print("=" * 80)

# Test 1: Tariff calculation
print("\n1. Tariff calculations:")
rural_tariff = TARIFFS["rural_domestic_up"]
print(f"   Rural domestic UP tariff: {rural_tariff.name}")
charge_100 = energy_charge(rural_tariff, 100.0)  # 100 kWh @ 3.35 Rs/kWh
charge_250 = energy_charge(rural_tariff, 250.0)  # 100@3.35 + 150@5.50
print(f"   100 kWh charge: ₹{charge_100:.2f} (expected ₹335.00)")
print(f"   250 kWh charge: ₹{charge_250:.2f} (expected ₹1160.00)")
assert abs(charge_100 - 335.0) < 0.01, "100 kWh charge incorrect"
assert abs(charge_250 - 1160.0) < 0.01, "250 kWh charge incorrect"
print("   ✓ Tariff slab calculation correct")

# Test 2: Subsidy application
print("\n2. Subsidy calculations:")
capex = {
    "solar": 45000.0 * 20.0,  # 20 kW @ 45k/kW = 900k
    "bess": 10000.0 * 50.0,   # 50 kWh @ 10k/kWh = 500k
    "router": 150000.0 * 5,   # 5 nodes @ 150k = 750k
    "cable": 800000.0 * 0.3,  # 0.3 km @ 800k/km = 240k
}
total_raw = sum(capex.values())
print(f"   Raw capex: ₹{total_raw:,.0f}")

stack = SubsidyStack(
    pm_surya_ghar=True,
    kusum_fls=True,
    bess_vgf_frac=0.30,
    rural_license_exempt=True,
)
capex_after, subsidies = apply_subsidies(capex.copy(), stack, n_households=3, pv_kw=20.0, is_agri_feeder=True)
total_after = sum(capex_after.values())
total_subsidies = sum(subsidies.values())
print(f"   Capex after subsidies: ₹{total_after:,.0f}")
print(f"   Total subsidies received: ₹{total_subsidies:,.0f}")
print(f"   Breakdown:")
for name, amt in subsidies.items():
    if amt > 0:
        print(f"     - {name}: ₹{amt:,.0f}")
print("   ✓ Subsidy application successful")

# Test 3: Financial calculations
print("\n3. Financial calculations:")
fin = FinanceConfig(discount_rate=0.10, project_life_years=25)
crf_val = crf(fin.discount_rate, fin.project_life_years)
print(f"   CRF @ 10% / 25 yr: {crf_val:.6f}")
expected_crf = 0.10 * (1.10)**25 / ((1.10)**25 - 1)
assert abs(crf_val - expected_crf) < 1e-6, "CRF calculation incorrect"
print("   ✓ CRF calculation correct")

# Test LCOE
annual_kwh = 50000.0  # 50 MWh/year
om_annual = total_after * 0.01
lcoe_val = lcoe(total_after, om_annual, annual_kwh, fin)
print(f"   LCOE: ₹{lcoe_val:.2f} / kWh")
expected_lcoe = (total_after * crf_val + om_annual) / annual_kwh
assert abs(lcoe_val - expected_lcoe) < 0.01, "LCOE calculation incorrect"
print("   ✓ LCOE calculation correct")

# Test 4: Market configs
print("\n4. Market configurations:")
for market_name, market in MARKETS.items():
    print(f"   {market_name.upper()}:")
    print(f"     Nodes: {list(market.node_cfg.keys())}")
    print(f"     Outage hours/day: {market.baseline_outage_hours_per_day:.1f}")
    print(f"     P2P price: ₹{market.p2p_price_rs_per_kwh:.2f}/kWh")
    print(f"     Subsidies: PM-SG={market.subsidies.pm_surya_ghar}, KUSUM={market.subsidies.kusum_fls}, BESS={market.subsidies.bess_vgf_frac:.0%}")

print("\n" + "=" * 80)
print("✓ ALL ECONOMICS LAYER TESTS PASSED")
print("=" * 80)
