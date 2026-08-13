import React from "react";
import { useStore } from "../store";

function fmt(val: unknown): string {
  if (val === null || val === undefined) return "—";
  if (typeof val === "number") {
    if (Math.abs(val) >= 1_000_000) return `₹${(val / 100_000).toFixed(1)}L`;
    if (Math.abs(val) >= 1_000) return `₹${(val / 1_000).toFixed(1)}K`;
    return val.toFixed(2);
  }
  return String(val);
}

function Metric({
  label,
  value,
  unit,
  color,
}: {
  label: string;
  value: unknown;
  unit?: string;
  color?: string;
}) {
  return (
    <div style={styles.metric}>
      <div style={{ color: "#888", fontSize: 10, marginBottom: 2 }}>{label}</div>
      <div style={{ color: color ?? "#fff", fontSize: 16, fontWeight: 700 }}>
        {fmt(value)}
        {unit && <span style={{ fontSize: 10, color: "#888", marginLeft: 2 }}>{unit}</span>}
      </div>
    </div>
  );
}

export default function ResultsPanel() {
  const result = useStore((s) => s.result);
  const playbackStep = useStore((s) => s.playbackStep);
  const setPlaybackStep = useStore((s) => s.setPlaybackStep);

  if (!result) {
    return (
      <div style={styles.panel}>
        <div style={styles.empty}>
          Run a simulation to see results here
        </div>
      </div>
    );
  }

  const e = result.economics as Record<string, any>;
  const m = result.metrics as Record<string, any>;
  const verdict = result.viable_vs_retail;
  const verdictColor =
    verdict === "green" ? "#4caf50" : verdict === "amber" ? "#ffc107" : "#ef5350";
  const verdictLabel =
    verdict === "green"
      ? "VIABLE"
      : verdict === "amber"
      ? "MARGINAL"
      : "NOT VIABLE";

  const totalSteps = result.timeseries.length;
  const currentTime = result.timeseries[playbackStep]?.t ?? "";

  return (
    <div style={styles.panel}>
      {/* Verdict badge */}
      <div style={{ ...styles.verdict, background: verdictColor }}>
        {verdictLabel}
      </div>
      <div style={{ color: "#888", fontSize: 10, textAlign: "center", marginBottom: 8 }}>
        Viable tariff: ₹{e.viable_tariff_rs_per_kwh}/kWh &nbsp;|&nbsp;
        Simulated in {result.sim_seconds}s
      </div>

      {/* Key metrics */}
      <div style={styles.metricsGrid}>
        <Metric label="LCOE" value={e.lcoe_mesh_rs_per_kwh} unit="₹/kWh" />
        <Metric label="Viable Tariff" value={e.viable_tariff_rs_per_kwh} unit="₹/kWh" color={verdictColor} />
        <Metric label="Payback" value={e.payback_years} unit="yr" />
        <Metric label="NPV" value={e.npv_rs} unit="" />
        <Metric label="Net Annual" value={e.net_annual_rs} unit=""
          color={e.net_annual_rs >= 0 ? "#4caf50" : "#ef5350"} />
        <Metric label="CAPEX (post-subsidy)" value={e.capex_after_subsidies_rs} unit="" />
      </div>

      {/* Physics metrics */}
      <div style={styles.sectionTitle}>PHYSICS</div>
      <div style={styles.metricsGrid}>
        <Metric label="Self-Sufficiency" value={m.self_sufficiency_pct} unit="%" />
        <Metric label="Self-Consumption" value={m.self_consumption_pct} unit="%" />
        <Metric label="Grid Import" value={m.grid_import_kwh} unit="kWh" />
        <Metric label="Grid Export" value={m.grid_export_kwh} unit="kWh" />
        <Metric label="Shed Load" value={m.shed_load_kwh} unit="kWh"
          color={m.shed_load_kwh > 0.1 ? "#ef5350" : "#4caf50"} />
        <Metric label="Diesel Fuel" value={m.generator_fuel_L} unit="L" />
      </div>

      {/* Revenue / Cost breakdown */}
      <div style={styles.sectionTitle}>P&L (ANNUAL)</div>
      <div style={styles.plRow}>
        <span style={styles.plLabel}>Energy Sales</span>
        <span style={{ color: "#4caf50" }}>{fmt(e.revenue_energy_sales_rs)}</span>
      </div>
      <div style={styles.plRow}>
        <span style={styles.plLabel}>P2P Export</span>
        <span style={{ color: "#4caf50" }}>{fmt(e.revenue_p2p_rs)}</span>
      </div>
      <div style={styles.plRow}>
        <span style={styles.plLabel}>Carbon Credits</span>
        <span style={{ color: "#4caf50" }}>{fmt(e.revenue_carbon_rs)}</span>
      </div>
      <div style={{ ...styles.plRow, borderTop: "1px solid #2a2a4a", paddingTop: 4 }}>
        <span style={styles.plLabel}>Annualized CAPEX+O&M</span>
        <span style={{ color: "#ef5350" }}>−{fmt(e.annual_cost_rs)}</span>
      </div>
      <div style={styles.plRow}>
        <span style={styles.plLabel}>Grid Import Cost</span>
        <span style={{ color: "#ef5350" }}>−{fmt(e.cost_grid_import_rs)}</span>
      </div>
      <div style={styles.plRow}>
        <span style={styles.plLabel}>Fuel Cost</span>
        <span style={{ color: "#ef5350" }}>−{fmt(e.cost_fuel_rs)}</span>
      </div>
      <div style={styles.plRow}>
        <span style={styles.plLabel}>VOLL Penalty</span>
        <span style={{ color: "#ef5350" }}>−{fmt(e.cost_voll_penalty_rs)}</span>
      </div>

      {/* Timeline scrubber */}
      <div style={styles.sectionTitle}>TIMELINE</div>
      <div style={{ color: "#7ecfff", fontSize: 11, marginBottom: 4 }}>
        Step {playbackStep + 1} / {totalSteps}
        {currentTime && <span> — {currentTime.split("T")[1]?.slice(0, 5) ?? currentTime}</span>}
      </div>
      <input
        type="range"
        min={0}
        max={totalSteps - 1}
        value={playbackStep}
        onChange={(e) => setPlaybackStep(parseInt(e.target.value))}
        style={{ width: "100%", accentColor: "#7ecfff" }}
      />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    width: 280,
    background: "#0a0a23",
    borderRight: "1px solid #1a1a4a",
    overflowY: "auto",
    padding: 12,
  },
  empty: {
    color: "#555",
    fontSize: 13,
    textAlign: "center",
    marginTop: 80,
    padding: 20,
    lineHeight: 1.6,
  },
  verdict: {
    textAlign: "center",
    color: "#fff",
    fontSize: 14,
    fontWeight: 800,
    padding: "6px 0",
    borderRadius: 6,
    marginBottom: 4,
    letterSpacing: 2,
  },
  metricsGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 6,
    marginBottom: 10,
  },
  metric: {
    background: "#16213e",
    borderRadius: 6,
    padding: "6px 8px",
  },
  sectionTitle: {
    color: "#666",
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: 1.5,
    marginBottom: 6,
    marginTop: 4,
    borderBottom: "1px solid #1a1a3a",
    paddingBottom: 3,
  },
  plRow: {
    display: "flex",
    justifyContent: "space-between",
    fontSize: 11,
    marginBottom: 3,
    padding: "2px 4px",
  },
  plLabel: {
    color: "#888",
  },
};
