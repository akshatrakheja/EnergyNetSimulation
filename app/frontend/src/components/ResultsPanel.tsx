import React from "react";
import { useStore } from "../store";

// ── Formatting ────────────────────────────────────────────────────────────────

function fmtRs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${v < 0 ? "−" : ""}₹${(abs / 100_000).toFixed(1)}L`;
  if (abs >= 1_000)     return `${v < 0 ? "−" : ""}₹${(abs / 1_000).toFixed(1)}K`;
  return `${v < 0 ? "−" : ""}₹${abs.toFixed(0)}`;
}

function fmtNum(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(decimals);
}

// ── Sub-components ────────────────────────────────────────────────────────────

function KV({ label, value, color, sub }: {
  label: string; value: React.ReactNode; color?: string; sub?: string;
}) {
  return (
    <div style={{
      background: "var(--surface)", borderRadius: "var(--radius)",
      border: "1px solid var(--border)", padding: "8px 10px",
    }}>
      <div style={{ color: "var(--text-muted)", fontSize: 10, marginBottom: 3, letterSpacing: "0.02em" }}>{label}</div>
      <div style={{ color: color ?? "var(--text)", fontSize: 15, fontWeight: 600, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums" }}>
        {value}
        {sub && <span style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 400, marginLeft: 3 }}>{sub}</span>}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      color: "var(--text-muted)", fontSize: 10, fontWeight: 600,
      letterSpacing: "0.08em", textTransform: "uppercase",
      marginBottom: 6, marginTop: 12, paddingBottom: 5,
      borderBottom: "1px solid var(--border)",
    }}>
      {children}
    </div>
  );
}

function PlRow({ label, value, positive }: { label: string; value: string; positive: boolean }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "3px 0", borderBottom: "1px solid var(--border)",
    }}>
      <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>{label}</span>
      <span style={{ color: positive ? "var(--green)" : "var(--red)", fontSize: 11, fontWeight: 500, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </span>
    </div>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────

export default function ResultsPanel() {
  const result       = useStore((s) => s.result);
  const playbackStep = useStore((s) => s.playbackStep);
  const setPlayback  = useStore((s) => s.setPlaybackStep);

  if (!result) {
    return (
      <div style={{
        width: 256, background: "var(--panel)", borderRight: "1px solid var(--border)",
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        padding: 24, gap: 8,
      }}>
        <div style={{ fontSize: 28, opacity: 0.3 }}>⚡</div>
        <div style={{ color: "var(--text-muted)", fontSize: 12, textAlign: "center", lineHeight: 1.6 }}>
          Configure nodes and run a simulation to see economics here
        </div>
      </div>
    );
  }

  const e = result.economics as Record<string, any>;
  const m = result.metrics as Record<string, any>;
  const verdict = result.viable_vs_retail;
  const verdictColor = verdict === "green" ? "var(--green)" : verdict === "amber" ? "var(--amber)" : "var(--red)";
  const verdictLabel = verdict === "green" ? "Viable" : verdict === "amber" ? "Marginal" : "Not Viable";

  const totalSteps = result.timeseries.length;
  const ts = result.timeseries[playbackStep]?.t ?? "";
  const timeLabel = ts.includes("T") ? ts.split("T")[1]?.slice(0, 5) : ts;

  return (
    <div style={{
      width: 256, background: "var(--panel)", borderRight: "1px solid var(--border)",
      overflowY: "auto", padding: "12px 10px 20px", flexShrink: 0,
    }}>

      {/* Verdict */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "8px 10px",
        background: `${verdictColor}10`,
        border: `1px solid ${verdictColor}30`,
        borderRadius: "var(--radius-md)",
        marginBottom: 6,
      }}>
        <div>
          <div style={{ color: verdictColor, fontSize: 13, fontWeight: 700, letterSpacing: "-0.01em" }}>
            {verdictLabel}
          </div>
          <div style={{ color: "var(--text-secondary)", fontSize: 10, marginTop: 1 }}>
            Viable tariff: <b style={{ color: "var(--text)" }}>₹{e.viable_tariff_rs_per_kwh}/kWh</b>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ color: "var(--text-muted)", fontSize: 9 }}>sim time</div>
          <div style={{ color: "var(--text-secondary)", fontSize: 11, fontVariantNumeric: "tabular-nums" }}>
            {result.sim_seconds}s
          </div>
        </div>
      </div>

      {/* Key metrics 2-col */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginBottom: 4 }}>
        <KV label="LCOE" value={`₹${e.lcoe_mesh_rs_per_kwh}`} sub="/kWh" />
        <KV label="Viable Tariff" value={`₹${e.viable_tariff_rs_per_kwh}`} sub="/kWh" color={verdictColor} />
        <KV label="Payback" value={e.payback_years != null ? e.payback_years : "∞"} sub="yr" />
        <KV label="NPV" value={fmtRs(e.npv_rs)} color={e.npv_rs >= 0 ? "var(--green)" : "var(--red)"} />
        <KV label="Net Annual" value={fmtRs(e.net_annual_rs)} color={e.net_annual_rs >= 0 ? "var(--green)" : "var(--red)"} />
        <KV label="CAPEX (net)" value={fmtRs(e.capex_after_subsidies_rs)} />
      </div>

      {/* Physics */}
      <SectionLabel>Physics</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginBottom: 4 }}>
        <KV label="Self-Sufficiency" value={`${fmtNum(m.self_sufficiency_pct)}%`} />
        <KV label="Self-Consumption" value={`${fmtNum(m.self_consumption_pct)}%`} />
        <KV label="Grid Import" value={fmtNum(m.grid_import_kwh)} sub="kWh" />
        <KV label="Grid Export" value={fmtNum(m.grid_export_kwh)} sub="kWh" />
        <KV label="Shed Load" value={fmtNum(m.shed_load_kwh)} sub="kWh"
          color={m.shed_load_kwh > 0.1 ? "var(--red)" : "var(--green)"} />
        <KV label="Diesel Used" value={fmtNum(m.generator_fuel_L)} sub="L" />
      </div>

      {/* P&L */}
      <SectionLabel>Annual P&L</SectionLabel>
      <PlRow label="Energy Sales"     value={fmtRs(e.revenue_energy_sales_rs)} positive />
      <PlRow label="P2P Export"       value={fmtRs(e.revenue_p2p_rs)}          positive />
      <PlRow label="Carbon Credits"   value={fmtRs(e.revenue_carbon_rs)}       positive />
      <PlRow label="CAPEX + O&M"      value={`−${fmtRs(e.annual_cost_rs)}`}    positive={false} />
      <PlRow label="Grid Import Cost" value={`−${fmtRs(e.cost_grid_import_rs)}`} positive={false} />
      <PlRow label="Fuel Cost"        value={`−${fmtRs(e.cost_fuel_rs)}`}      positive={false} />
      <PlRow label="VOLL Penalty"     value={`−${fmtRs(e.cost_voll_penalty_rs)}`} positive={false} />

      {/* Timeline */}
      <SectionLabel>Timeline</SectionLabel>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginBottom: 5,
      }}>
        <span style={{ color: "var(--text-muted)", fontSize: 10, fontVariantNumeric: "tabular-nums" }}>
          {timeLabel}
        </span>
        <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
          {playbackStep + 1} / {totalSteps}
        </span>
      </div>
      <input
        type="range"
        min={0} max={totalSteps - 1} value={playbackStep}
        onChange={(e) => setPlayback(parseInt(e.target.value))}
        style={{
          width: "100%",
          background: `linear-gradient(to right, var(--accent) ${(playbackStep / (totalSteps - 1)) * 100}%, var(--surface) ${(playbackStep / (totalSteps - 1)) * 100}%)`,
        }}
        className="accent"
      />
    </div>
  );
}
