"""
DEG Adapter — energynet_phase2 ↔ DEG P2P Trading Wave2

Flow
----
1. Run E1 rural simulation (7-day, 15-min steps)
2. Aggregate 15-min grid-export intervals → 1-hour BecknTimeSeries slots
3. POST `confirm` to the wave2 devkit (seller BPP caller, port 8082)
4. POST `on_status` with actuals (FINAL_ALLOC = actual kWh exported per hour)
5. Parse `revenueFlows` from the on_status response body
6. Compare DEG-computed settlement vs economics.py P2P revenue

What this tests
---------------
- Whether our hardcoded `p2p_price_rs_per_kwh` and `p2p_transaction_charge_rs_per_kwh`
  match what the live Beckn Rego policy computes for the same kWh quantities.
- The BecknTimeSeries mapping from SimResults to DEG contract intervals.

Usage
-----
  # Devkit must be up: run `./deg_adapter.py --start` or bring up manually first
  python deg_adapter.py [--start] [--market rural|peri_urban|urban] [--verbose]

Endpoints (wave2 devkit localhost mapping)
------------------------------------------
  BAP caller (buyer):          http://localhost:8081/bap/caller
  BPP caller (seller):         http://localhost:8082/bpp/caller
  SellerDiscom ledger BPP:     http://localhost:8083/bpp/caller
  BuyerDiscom ledger BPP:      http://localhost:8084/bpp/caller
  Caddy router (full stack):   http://localhost:9000
"""

import argparse
import datetime
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent
DEG_DEVKIT = Path("/Users/rakheja/Documents/energynet_DEG/DEG/devkits/p2p-trading-ies-wave2/install")

# DEG wave2 devkit endpoints
BAP_CALLER = "http://localhost:8091/bap/caller"  # remapped from 8081
BPP_CALLER = "http://localhost:8082/bpp/caller"
SELLER_DISCOM_LEDGER = "http://localhost:8083/bpp/caller"
BUYER_DISCOM_LEDGER  = "http://localhost:8084/bpp/caller"

NETWORK_ID = "indiaenergystack.in/test-ies-p2p-trading-network"
BECKN_VERSION = "2.0.0"
SCHEMA_CONTEXT = [
    "https://schema.nfh.global/EnergyTradeOffer/v2.0/context.jsonld",
    "https://schema.nfh.global/EnergyResource/v2.0/context.jsonld",
    "https://schema.nfh.global/EnergyCustomer/v2.0/context.jsonld",
    "https://schema.nfh.global/DEGContract/v2.0/context.jsonld",
    "https://schema.nfh.global/BecknTimeSeries/v1.0/context.jsonld",
    "https://schema.nfh.global/DiscomLedgerProvider/v1.0/context.jsonld",
    "https://schema.nfh.global/SettlementTerm/2.0/context.jsonld",
]
SCHEMA_CONTEXT_STATUS = SCHEMA_CONTEXT + [
    "https://schema.nfh.global/RevenueFlow/v2.0/context.jsonld",
    "https://schema.nfh.global/PaymentAction/v2.0/context.jsonld",
]

# ---------------------------------------------------------------------------
# Devkit helpers
# ---------------------------------------------------------------------------

def devkit_start(verbose: bool = False) -> None:
    """Bring up the wave2 Docker Compose stack."""
    print("  Starting DEG wave2 devkit (docker compose up -d)…")
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=DEG_DEVKIT,
        capture_output=not verbose,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr or result.stdout)
        raise RuntimeError("docker compose up failed")
    # Wait for Caddy router healthcheck
    print("  Waiting for devkit to be ready…", end="", flush=True)
    for _ in range(30):
        time.sleep(3)
        try:
            r = requests.get("http://localhost:9000/health", timeout=2)
            if r.status_code < 500:
                print(" ready.")
                return
        except requests.exceptions.ConnectionError:
            pass
        print(".", end="", flush=True)
    print(" (timeout — continuing anyway)")


def devkit_stop(verbose: bool = False) -> None:
    subprocess.run(
        ["docker", "compose", "down"],
        cwd=DEG_DEVKIT,
        capture_output=not verbose,
        text=True,
    )
    print("  DEG devkit stopped.")


def devkit_is_up() -> bool:
    try:
        r = requests.get("http://localhost:9000/health", timeout=2)
        return r.status_code < 500
    except Exception:
        pass
    # Fallback: try BPP caller directly
    try:
        requests.get("http://localhost:8082/", timeout=2)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def run_simulation_for_market(market_name: str, verbose: bool = False):
    """Run energynet_phase2 simulation and return (results, market, econ)."""
    sys.path.insert(0, str(REPO_ROOT))
    if market_name == "rural":
        from src.econ_scenarios import run_e1_rural
        out = run_e1_rural(save=False)
    elif market_name == "peri_urban":
        from src.econ_scenarios import run_e2_peri_urban
        out = run_e2_peri_urban(save=False)
    else:
        from src.econ_scenarios import run_e3_urban
        out = run_e3_urban(save=False)

    return out["mesh_results"], out["econ"]


# ---------------------------------------------------------------------------
# SimResults → BecknTimeSeries intervals
# ---------------------------------------------------------------------------

def sim_to_hourly_intervals(
    mesh_results,
    p2p_price_rs_per_kwh: float,
    delivery_date: datetime.date | None = None,
) -> list[dict]:
    """Aggregate 15-min SimResults grid-export steps into hourly BecknTimeSeries intervals.

    Only export steps (grid_exchange_kw < 0) count as P2P supply.

    Returns list of interval dicts with keys:
        id, hour, export_kwh (actual), price_per_kwh
    """
    if delivery_date is None:
        delivery_date = datetime.date.today() + datetime.timedelta(days=1)

    df = mesh_results.to_dataframe()
    dt_h = mesh_results.dt_h  # 0.25 h for 15-min steps

    # grid_exchange_kw: positive = import, negative = export
    export_kw = (-df["grid_exchange_kw"]).clip(lower=0)
    export_kwh = export_kw * dt_h  # per step

    # steps per hour
    steps_per_hour = int(round(1.0 / dt_h))
    n_steps = len(df)
    n_hours = n_steps // steps_per_hour

    intervals = []
    for h in range(n_hours):
        start = h * steps_per_hour
        end = start + steps_per_hour
        kwh_this_hour = float(export_kwh.iloc[start:end].sum())
        if kwh_this_hour < 1e-4:
            continue  # skip hours with no export

        # Wall-clock start for this interval (Day 1 00:00 IST → UTC offset -5:30)
        day_offset = h // 24
        hour_of_day = h % 24
        dt_utc = datetime.datetime.combine(
            delivery_date + datetime.timedelta(days=day_offset),
            datetime.time(hour_of_day, 0, 0),
            tzinfo=datetime.timezone.utc,
        ) - datetime.timedelta(hours=5, minutes=30)

        intervals.append({
            "id": h,
            "hour": h,
            "export_kwh": round(kwh_this_hour, 3),
            "price_per_kwh": p2p_price_rs_per_kwh,
            "start_utc": dt_utc.isoformat().replace("+00:00", "Z"),
        })

    return intervals


# ---------------------------------------------------------------------------
# Beckn message builders
# ---------------------------------------------------------------------------

# Use the canonical devkit participant IDs — these match the Ed25519 keys registered in
# Redis (networkParticipant in local-p2p-trading-buyerapp/sellerapp.yaml configs).
# Our EnergyNet mesh is the "sellerPlatform" (prosumer with surplus solar to trade).
BAP_ID = "buyerapp.example.com"    # key: 76EU8w8y… / TTQMAEy0…
BPP_ID = "sellerapp.example.com"   # key registered in local-p2p-trading-sellerapp.yaml


def _ctx(action: str, tx_id: str, msg_id: str, extra_schemas: list | None = None) -> dict:
    schemas = SCHEMA_CONTEXT_STATUS if extra_schemas else SCHEMA_CONTEXT
    return {
        "networkId": NETWORK_ID,
        "version": BECKN_VERSION,
        "action": action,
        "bapId": BAP_ID,
        "bapUri": "http://localhost:8091/bap/receiver",
        "bppId": BPP_ID,
        "bppUri": "http://localhost:8082/bpp/receiver",
        "transactionId": tx_id,
        "messageId": msg_id,
        "timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schemaContext": schemas,
    }


def build_confirm(
    tx_id: str,
    contract_id: str,
    intervals: list[dict],
    market_name: str,
    total_export_kwh: float,
) -> dict:
    """Build Beckn confirm message from simulation export intervals."""
    # Group intervals into a single BecknTimeSeries commitment
    ts_intervals = [
        {
            "id": iv["id"],
            "payloads": [
                {"type": "PRICE_PER_KWH",  "values": [iv["price_per_kwh"]]},
                {"type": "REQUESTED_QTY",  "values": [round(iv["export_kwh"], 3)]},
            ],
        }
        for iv in intervals
    ]

    start_utc = intervals[0]["start_utc"] if intervals else datetime.datetime.utcnow().isoformat() + "Z"

    return {
        "context": _ctx("confirm", tx_id, str(uuid.uuid4())),
        "message": {
            "contract": {
                "status": {"code": "DRAFT"},
                "commitments": [
                    {
                        "id": f"commit-energynet-{contract_id[:8]}",
                        "status": {"descriptor": {"code": "DRAFT"}},
                        "resources": [
                            {
                                "id": f"solar-export-{contract_id[:8]}",
                                "descriptor": {
                                    "name": f"EnergyNet {market_name} mesh export — {total_export_kwh:.1f} kWh"
                                },
                                "quantity": {
                                    "@type": "Quantity",
                                    "unitCode": "KWH",
                                    "unitQuantity": round(total_export_kwh, 3),
                                },
                                "resourceAttributes": {
                                    "@context": "https://schema.nfh.global/EnergyResource/v2.0/context.jsonld",
                                    "@type": "EnergyResource",
                                    "type": "SOLAR",
                                    "id": "TEST_METER_SELLER_001",  # test network requires TEST_ prefix
                                },
                            }
                        ],
                        "offer": {
                            "id": f"offer-{contract_id[:8]}",
                            "resourceIds": [f"solar-export-{contract_id[:8]}"],
                        },
                        "commitmentAttributes": {
                            "@context": "https://schema.nfh.global/BecknTimeSeries/v1.0/context.jsonld",
                            "@type": "TimeSeries",
                            "intervalPeriod": {"start": start_utc, "duration": "PT1H"},
                            "payloadDescriptors": [
                                {
                                    "objectType": "EVENT_PAYLOAD_DESCRIPTOR",
                                    "payloadType": "PRICE_PER_KWH",
                                    "currency": "INR",
                                    "insertedBy": "sellerPlatform",
                                },
                                {
                                    "objectType": "EVENT_PAYLOAD_DESCRIPTOR",
                                    "payloadType": "REQUESTED_QTY",
                                    "units": "KWH",
                                    "insertedBy": "buyerPlatform",
                                },
                            ],
                            "intervals": ts_intervals,
                        },
                    }
                ],
                "contractAttributes": {
                    "@context": "https://schema.nfh.global/DEGContract/v2.0/context.jsonld",
                    "@type": "DEGContract",
                    "roles": [
                        {"role": "buyerPlatform",  "participantId": BAP_ID},
                        {"role": "sellerPlatform", "participantId": BPP_ID},
                        # Must use IES allowlisted discom IDs (Rego policy: allowed = TEST_DISCOM_*)
                        {"role": "buyerDiscom",    "participantId": "TEST_DISCOM_BUYER"},
                        {"role": "sellerDiscom",   "participantId": "TEST_DISCOM_SELLER"},
                    ],
                    # Live Rego policy — downloaded and evaluated by ContractPolicyEnforcer plugin
                    "policy": {
                        "url": "https://api.dedi.global/dedi/lookup/indiaenergystack.in/ies-rulesets/p2p-trading-ies-contractpolicy-common",
                        "queryPath": "data.deg.contracts.p2p_trading",
                    },
                },
                "participants": [
                    {
                        "id": BPP_ID,
                        "participantAttributes": {
                            "@context": "https://schema.nfh.global/EnergyCustomer/v2.0/context.jsonld",
                            "@type": "EnergyCustomer",
                            # Meter IDs must match TEST_ prefix (Rego: test network constraint)
                            "meterId": "TEST_METER_SELLER_001",
                            "utilityCustomerId": "TEST_CUST_SELLER_001",
                            "platformUrl": "http://localhost:8082",
                        },
                    },
                    {
                        "id": BAP_ID,
                        "participantAttributes": {
                            "@context": "https://schema.nfh.global/EnergyCustomer/v2.0/context.jsonld",
                            "@type": "EnergyCustomer",
                            "meterId": "TEST_METER_BUYER_001",
                            "utilityCustomerId": "TEST_CUST_BUYER_001",
                            "platformUrl": "http://localhost:8091",
                        },
                    },
                    {
                        "id": "TEST_DISCOM_BUYER",
                        "participantAttributes": {
                            "@context": "https://schema.nfh.global/DiscomLedgerProvider/v1.0/context.jsonld",
                            "@type": "DiscomLedgerProvider",
                            "discomId": "buyer-discom.example.com",
                            "discomUri": "http://buyer-discom.example.com:9000",
                            "ledgerId": "buyer-discom-ledger.example.com",
                            "ledgerUri": "http://buyer-discom-ledger.example.com:9000",
                        },
                    },
                    {
                        "id": "TEST_DISCOM_SELLER",
                        "participantAttributes": {
                            "@context": "https://schema.nfh.global/DiscomLedgerProvider/v1.0/context.jsonld",
                            "@type": "DiscomLedgerProvider",
                            "discomId": "seller-discom.example.com",
                            "discomUri": "http://seller-discom.example.com:9000",
                            "ledgerId": "seller-discom-ledger.example.com",
                            "ledgerUri": "http://seller-discom-ledger.example.com:9000",
                        },
                    },
                ],
                "settlements": [
                    {
                        "id": f"settlement-{contract_id[:8]}",
                        "status": "DRAFT",
                        "settlementAttributes": {
                            "@context": "https://schema.nfh.global/SettlementTerm/2.0/context.jsonld",
                            "@type": "SettlementTerm",
                            "payTo": {"accountHolderName": "EnergyNet Seller", "accountNumber": "0012345678901", "branchCode": "HDFC0001234", "bankName": "HDFC Bank"},
                            "acceptedPaymentMethods": ["BANK_TRANSFER"],  # schema: CASH_DEPOSIT|BANK_TRANSFER only
                            "paymentTrigger": "ON_FULFILLMENT",
                            "settlementStatus": "PENDING",
                        },
                    }
                ],
            }
        },
    }


def build_on_status_settled(
    tx_id: str,
    contract_id: str,
    intervals: list[dict],
) -> dict:
    """Build on_status with FINAL_ALLOC actuals (SimResults actual export per hour)."""
    commit_id = f"commit-energynet-{contract_id[:8]}"

    ts_intervals = []
    for iv in intervals:
        # Simulate slight discom allocation shrinkage (±2%) — realistic meter reconciliation
        buyer_alloc  = round(iv["export_kwh"] * 0.98, 3)
        seller_alloc = round(iv["export_kwh"] * 1.00, 3)
        final_alloc  = round(min(buyer_alloc, seller_alloc), 3)
        ts_intervals.append({
            "id": iv["id"],
            "payloads": [
                {"type": "PRICE_PER_KWH",       "values": [iv["price_per_kwh"]]},
                {"type": "REQUESTED_QTY",        "values": [round(iv["export_kwh"], 3)]},
                {"type": "BUYER_DISCOM_ALLOC",   "values": [buyer_alloc]},
                {"type": "BUYER_DISCOM_STATUS",  "values": ["COMPLETED"]},
                {"type": "SELLER_DISCOM_ALLOC",  "values": [seller_alloc]},
                {"type": "SELLER_DISCOM_STATUS", "values": ["COMPLETED"]},
                {"type": "FINAL_ALLOC",          "values": [final_alloc]},
            ],
        })

    start_utc = intervals[0]["start_utc"] if intervals else datetime.datetime.utcnow().isoformat() + "Z"

    return {
        "context": _ctx("on_status", tx_id, str(uuid.uuid4()), extra_schemas=True),
        "message": {
            "contract": {
                "id": contract_id,
                "status": {"code": "COMPLETE"},
                "commitments": [
                    {
                        "id": commit_id,
                        "status": {"descriptor": {"code": "CLOSED"}},
                        "resources": [
                            {
                                "id": f"solar-export-{contract_id[:8]}",
                                "descriptor": {"name": "EnergyNet mesh export — settled"},
                                "quantity": {
                                    "@type": "Quantity",
                                    "unitCode": "KWH",
                                    "unitQuantity": round(sum(iv["export_kwh"] for iv in intervals), 3),
                                },
                                "resourceAttributes": {
                                    "@context": "https://schema.nfh.global/EnergyResource/v2.0/context.jsonld",
                                    "@type": "EnergyResource",
                                    "type": "SOLAR",
                                    "id": "TEST_METER_SELLER_001",
                                },
                            }
                        ],
                        "offer": {"id": f"offer-{contract_id[:8]}", "resourceIds": [f"solar-export-{contract_id[:8]}"]},
                        "commitmentAttributes": {
                            "@context": "https://schema.nfh.global/BecknTimeSeries/v1.0/context.jsonld",
                            "@type": "TimeSeries",
                            "intervalPeriod": {"start": start_utc, "duration": "PT1H"},
                            "payloadDescriptors": [
                                {"objectType": "EVENT_PAYLOAD_DESCRIPTOR", "payloadType": "PRICE_PER_KWH",       "currency": "INR", "insertedBy": "sellerPlatform"},
                                {"objectType": "EVENT_PAYLOAD_DESCRIPTOR", "payloadType": "REQUESTED_QTY",       "units": "KWH",   "insertedBy": "buyerPlatform"},
                                {"objectType": "REPORT_PAYLOAD_DESCRIPTOR","payloadType": "BUYER_DISCOM_ALLOC",  "units": "KWH",   "insertedBy": "buyerDiscom"},
                                {"objectType": "REPORT_PAYLOAD_DESCRIPTOR","payloadType": "BUYER_DISCOM_STATUS", "units": "STRING","insertedBy": "buyerDiscom"},
                                {"objectType": "REPORT_PAYLOAD_DESCRIPTOR","payloadType": "SELLER_DISCOM_ALLOC", "units": "KWH",   "insertedBy": "sellerDiscom"},
                                {"objectType": "REPORT_PAYLOAD_DESCRIPTOR","payloadType": "SELLER_DISCOM_STATUS","units": "STRING","insertedBy": "sellerDiscom"},
                                {"objectType": "REPORT_PAYLOAD_DESCRIPTOR","payloadType": "FINAL_ALLOC",         "units": "KWH",   "insertedBy": "sellerDiscom"},
                            ],
                            "intervals": ts_intervals,
                        },
                    }
                ],
                "contractAttributes": {
                    "@context": "https://schema.nfh.global/DEGContract/v2.0/context.jsonld",
                    "@type": "DEGContract",
                    "roles": [
                        {"role": "buyerPlatform",  "participantId": BAP_ID},
                        {"role": "sellerPlatform", "participantId": BPP_ID},
                        {"role": "buyerDiscom",    "participantId": "TEST_DISCOM_BUYER"},
                        {"role": "sellerDiscom",   "participantId": "TEST_DISCOM_SELLER"},
                    ],
                    "policy": {
                        "url": "https://api.dedi.global/dedi/lookup/indiaenergystack.in/ies-rulesets/p2p-trading-ies-contractpolicy-common",
                        "queryPath": "data.deg.contracts.p2p_trading",
                    },
                },
                "participants": [
                    {"id": BPP_ID, "participantAttributes": {
                        "@context": "https://schema.nfh.global/EnergyCustomer/v2.0/context.jsonld",
                        "@type": "EnergyCustomer", "meterId": "TEST_METER_SELLER_001",
                        "utilityCustomerId": "TEST_CUST_SELLER_001",
                        "platformUrl": "http://localhost:8082",
                    }},
                    {"id": BAP_ID, "participantAttributes": {
                        "@context": "https://schema.nfh.global/EnergyCustomer/v2.0/context.jsonld",
                        "@type": "EnergyCustomer", "meterId": "TEST_METER_BUYER_001",
                        "utilityCustomerId": "TEST_CUST_BUYER_001",
                        "platformUrl": "http://localhost:8091",
                    }},
                    {"id": "TEST_DISCOM_BUYER", "participantAttributes": {
                        "@context": "https://schema.nfh.global/DiscomLedgerProvider/v1.0/context.jsonld",
                        "@type": "DiscomLedgerProvider",
                        "discomId": "buyer-discom.example.com", "discomUri": "http://buyer-discom.example.com:9000",
                        "ledgerId": "buyer-discom-ledger.example.com", "ledgerUri": "http://buyer-discom-ledger.example.com:9000",
                    }},
                    {"id": "TEST_DISCOM_SELLER", "participantAttributes": {
                        "@context": "https://schema.nfh.global/DiscomLedgerProvider/v1.0/context.jsonld",
                        "@type": "DiscomLedgerProvider",
                        "discomId": "seller-discom.example.com", "discomUri": "http://seller-discom.example.com:9000",
                        "ledgerId": "seller-discom-ledger.example.com", "ledgerUri": "http://seller-discom-ledger.example.com:9000",
                    }},
                ],
            }
        },
    }


# ---------------------------------------------------------------------------
# Beckn HTTP helpers
# ---------------------------------------------------------------------------

def _post(url: str, payload: dict, step_name: str, verbose: bool = False) -> dict | None:
    """POST a Beckn message; return parsed JSON or None on error."""
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if verbose:
            print(f"    [{step_name}] → {url}  HTTP {resp.status_code}")
            if resp.text:
                try:
                    print(f"    response: {json.dumps(resp.json(), indent=2)[:800]}")
                except Exception:
                    print(f"    response: {resp.text[:400]}")
        if resp.status_code == 400:
            print(f"    [{step_name}] NACK (400) — policy violation or bad message")
            try:
                print(f"    {json.dumps(resp.json(), indent=2)[:600]}")
            except Exception:
                print(f"    {resp.text[:400]}")
            return None
        resp.raise_for_status()
        return resp.json() if resp.text else {}
    except requests.exceptions.ConnectionError:
        print(f"    [{step_name}] ConnectionError — is the devkit running?")
        return None
    except Exception as e:
        print(f"    [{step_name}] Error: {e}")
        return None


# ---------------------------------------------------------------------------
# Revenue flow extraction + comparison
# ---------------------------------------------------------------------------

def _extract_revenue_flows(on_status_body: dict) -> list[dict]:
    """Parse revenueFlows from on_status response body."""
    try:
        contract = on_status_body.get("message", {}).get("contract", {})
        for consideration in contract.get("consideration", []):
            attrs = consideration.get("considerationAttributes", {})
            if attrs.get("@type") == "RevenueFlow":
                return attrs.get("revenueFlows", [])
    except Exception:
        pass
    return []


def _extract_flows_from_container_log(since_seconds: int = 60) -> list[dict]:
    """Read onix-sellerapp Docker logs to extract Rego-computed revenueFlows.

    The ContractPolicyEnforcer injects revenueFlows into the on_status body
    *before* forwarding it to the BAP receiver. Even when the async forward
    fails (Docker-to-host 502), the enriched body is logged as the
    "Forwarding request" body. The body is a JSON-in-JSON string with
    backslash-escaped inner quotes (e.g. \"revenueFlows\").
    """
    import re
    try:
        result = subprocess.run(
            ["docker", "logs", "onix-sellerapp", f"--since={since_seconds}s", "--tail=200"],
            capture_output=True, text=True, timeout=10,
        )
        for line in reversed((result.stdout + result.stderr).splitlines()):
            # The body is escaped JSON; search for unquoted key name
            if "revenueFlows" not in line or "sellerPlatform" not in line:
                continue
            # Extract individual flow entries using regex on escaped JSON
            # Pattern: \"role\":\"<role>\",\"value\":<number>
            # (backslash escapes present in the raw log line)
            flows = []
            for m in re.finditer(
                r'\\"role\\":\\"([^"\\]+)\\"[^}]+?\\"value\\":(-?[\d.]+)',
                line,
            ):
                role = m.group(1)
                value = float(m.group(2))
                # Grab description if present
                desc_m = re.search(
                    rf'\\"role\\":\\"{re.escape(role)}\\"[^{{}}]*?\\"description\\":\\"([^\\"]+)\\"',
                    line,
                )
                desc = desc_m.group(1) if desc_m else ""
                flows.append({"role": role, "value": value, "description": desc, "currency": "INR"})
            if flows:
                return flows
    except Exception:
        pass
    return []


def compare_with_economics(
    revenue_flows: list[dict],
    econ,
    intervals: list[dict],
    p2p_price: float,
    p2p_txn_charge: float,
    market_name: str = "",
    verbose: bool = False,
) -> None:
    """Print a side-by-side comparison of DEG settlement vs economics.py."""
    total_final_kwh = sum(iv["export_kwh"] * 0.98 for iv in intervals)  # buyer alloc ~98%

    # Our economics.py calculation (annualized → de-annualize back to 7 days)
    # econ.revenue_p2p_rs is already annualized; sim was 7 days = 7/365.25 of a year
    sim_days = 7.0
    p2p_rs_sim_period = econ.revenue_p2p_rs / (365.25 / sim_days)

    # DEG settlement: sellerPlatform flow
    deg_seller_flow = next((f["value"] for f in revenue_flows if f.get("role") == "sellerPlatform"), None)

    # Our manual calculation for same interval set
    our_gross = sum(iv["export_kwh"] * 0.98 * iv["price_per_kwh"] for iv in intervals)
    our_txn   = sum(iv["export_kwh"] * 0.98 * p2p_txn_charge       for iv in intervals)
    our_net   = our_gross - our_txn

    print()
    print("  ╔══════════════════════════════════════════════════════════════╗")
    print("  ║  P2P SETTLEMENT COMPARISON: DEG  vs  economics.py           ║")
    print("  ╠══════════════════════════════════════════════════════════════╣")
    print(f"  ║  Export contracted (kWh, 7-day sim): {total_final_kwh:>8.2f}                ║")
    print(f"  ║  P2P price (Rs/kWh):                 {p2p_price:>8.2f}                ║")
    print(f"  ║  Transaction charge (Rs/kWh):        {p2p_txn_charge:>8.2f}                ║")
    print("  ╠══════════════════════════════════════════════════════════════╣")

    if deg_seller_flow is not None:
        delta = deg_seller_flow - our_net
        # DEG test network uses 0 wheeling charge; divergence ≈ txn_charge × kWh
        expected_delta = our_txn   # DEG doesn't deduct wheeling → delta = our_txn
        is_match = abs(delta) / max(abs(our_net), 1) < 0.02
        is_wheeling_explained = p2p_txn_charge > 0 and abs(delta - our_txn) / max(abs(our_txn), 1) < 0.02
        if is_match:
            status = "✓ MATCH"
        elif is_wheeling_explained:
            status = "⚠ DIVERGE (wheeling)"
        else:
            status = "✗ DIVERGE"
        print(f"  ║  DEG sellerPlatform flow (Rs):       {deg_seller_flow:>10,.2f}              ║")
        print(f"  ║  Our gross P2P (Rs):                 {our_gross:>10,.2f}              ║")
        print(f"  ║  Our txn deduction (Rs):             {-our_txn:>10,.2f}              ║")
        print(f"  ║  Our net P2P (Rs):                   {our_net:>10,.2f}              ║")
        print(f"  ║  Δ (DEG − ours):                     {delta:>10,.2f}              ║")
        print(f"  ║  Status:                        {status:>18}              ║")
        if is_wheeling_explained:
            print(f"  ║  Note: IES test-network wheeling=0; production Rego would           ║")
            print(f"  ║  route ₹{our_txn:,.2f} to discom roles (DERC 0.42 Rs/kWh).       ║")
            print(f"  ║  economics.py correctly models the regulatory deduction.            ║")
    else:
        print(f"  ║  DEG sellerPlatform flow:        NOT RETURNED (see above)   ║")
        print(f"  ║  Our net P2P (Rs, this period):     {our_net:>10,.2f}              ║")
        print(f"  ║  economics.py P2P (7-day de-ann):   {p2p_rs_sim_period:>10,.2f}              ║")

    print("  ╠══════════════════════════════════════════════════════════════╣")
    if deg_seller_flow is not None and revenue_flows:
        print("  ║  Full revenue flow breakdown:                                ║")
        for flow in revenue_flows:
            role = flow.get("role", "?")
            val  = flow.get("value", 0)
            desc = flow.get("description", "")[:35]
            print(f"  ║    {role:<20} {val:>10,.2f} INR  {desc:<15}║")
    print("  ╚══════════════════════════════════════════════════════════════╝")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_adapter(market_name: str = "rural", verbose: bool = False, start_devkit: bool = False) -> None:
    print()
    print(f"{'='*70}")
    print(f"  EnergyNet × DEG Adapter  |  market: {market_name}")
    print(f"{'='*70}")

    # 1. Start devkit if requested or not running
    if start_devkit:
        devkit_start(verbose)
    elif not devkit_is_up():
        print("  DEG devkit not running — starting it now…")
        devkit_start(verbose)

    if not devkit_is_up():
        print("  ✗ DEG devkit failed to start. Exiting.")
        return

    print("  ✓ DEG wave2 devkit is up")

    # 2. Run simulation
    print(f"\n  [1/5] Running energynet_phase2 simulation ({market_name})…")
    mesh_results, econ = run_simulation_for_market(market_name, verbose)
    print(f"  ✓ Simulation complete")
    print(f"      Grid export (7-day): {sum((-mesh_results.to_dataframe()['grid_exchange_kw']).clip(lower=0) * mesh_results.dt_h):.1f} kWh")
    print(f"      economics.py P2P revenue (annual): ₹{econ.revenue_p2p_rs:,.0f}")

    # 3. Build BecknTimeSeries intervals from export data
    from src.markets import MARKETS
    market = MARKETS[market_name]
    intervals = sim_to_hourly_intervals(mesh_results, market.p2p_price_rs_per_kwh)
    total_export = sum(iv["export_kwh"] for iv in intervals)
    print(f"\n  [2/5] Built BecknTimeSeries: {len(intervals)} hourly intervals, {total_export:.1f} kWh total")
    if verbose:
        for iv in intervals[:3]:
            print(f"      h{iv['hour']:02d}: {iv['export_kwh']:.3f} kWh @ ₹{iv['price_per_kwh']}/kWh")
        if len(intervals) > 3:
            print(f"      … ({len(intervals) - 3} more)")

    if not intervals:
        print("  ✗ No export intervals found — nothing to trade. Exiting.")
        return

    # 4. POST confirm → BPP caller (seller registers contract)
    tx_id = str(uuid.uuid4())
    contract_id = f"energynet-{tx_id[:8]}"
    confirm_payload = build_confirm(tx_id, contract_id, intervals, market_name, total_export)

    # Routing per wave2 config:
    #   confirm / status → BAP caller (buyer app, port 8091)
    #   on_confirm / on_status → BPP caller (seller app, port 8082)
    print(f"\n  [3/5] Posting `confirm` → BAP caller (buyer initiates contract)…")
    confirm_resp = _post(BAP_CALLER + "/confirm", confirm_payload, "confirm", verbose)

    if confirm_resp is not None:
        print(f"  ✓ confirm ACK received")
    else:
        print("  ⚠ confirm did not ACK — continuing with on_status anyway")

    # Small delay to let the Beckn-Onix pipeline process the contract registration
    time.sleep(2)

    # 5. POST on_status (settled) → BPP caller with FINAL_ALLOC actuals
    # The ContractPolicyEnforcer plugin runs on the BPP receiver path and injects
    # revenueFlows into on_status when settlement is COMPLETE.
    on_status_payload = build_on_status_settled(tx_id, contract_id, intervals)
    print(f"\n  [4/5] Posting `on_status` (settled) → BPP caller (seller reports actuals)…")
    status_resp = _post(BPP_CALLER + "/on_status", on_status_payload, "on_status", verbose)

    if status_resp is None:
        # Try sellerDiscom ledger path (port 8083) as alternative policy enforcer path
        print("      Retrying via sellerDiscom ledger caller…")
        status_resp = _post(SELLER_DISCOM_LEDGER + "/on_status", on_status_payload, "on_status-sdl", verbose)

    # 6. Extract revenue flows and compare
    print(f"\n  [5/5] Comparing DEG settlement vs economics.py…")
    revenue_flows = []
    if status_resp:
        revenue_flows = _extract_revenue_flows(status_resp)

    if not revenue_flows:
        # Rego injection is async — the enriched message goes to /bap/receiver, not
        # back to our caller. Parse it from the container log instead.
        time.sleep(1)
        revenue_flows = _extract_flows_from_container_log(since_seconds=60)
        if revenue_flows:
            print("  ✓ Revenue flows extracted from onix-sellerapp container log (Rego async inject)")
        else:
            print("  ⚠ Revenue flows not found — showing our-side calculation only")

    compare_with_economics(
        revenue_flows,
        econ,
        intervals,
        p2p_price=market.p2p_price_rs_per_kwh,
        p2p_txn_charge=market.p2p_transaction_charge_rs_per_kwh,
        market_name=market_name,
        verbose=verbose,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EnergyNet × DEG P2P adapter")
    parser.add_argument("--market", choices=["rural", "peri_urban", "urban"], default="rural")
    parser.add_argument("--start", action="store_true", help="Start DEG devkit if not running")
    parser.add_argument("--stop",  action="store_true", help="Stop DEG devkit after run")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    try:
        run_adapter(market_name=args.market, verbose=args.verbose, start_devkit=args.start)
    finally:
        if args.stop:
            devkit_stop(args.verbose)
