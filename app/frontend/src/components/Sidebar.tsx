import React, { useCallback, useRef, useState } from "react";
import { useStore } from "../store";
import { runSimulation, recalcEconomics } from "../api";
import type { EconomicsSpec } from "../store";

// ── Primitives ───────────────────────────────────────────────────────────────

function Slider({
  label, value, min, max, step, unit, onChange,
}: {
  label: string; value: number; min: number; max: number;
  step: number; unit?: string; onChange: (v: number) => void;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 5 }}>
        <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>{label}</span>
        <span style={{
          color: "var(--text)", fontSize: 12, fontWeight: 500,
          fontVariantNumeric: "tabular-nums", letterSpacing: "-0.01em",
        }}>
          {value}{unit ?? ""}
        </span>
      </div>
      <div style={{ position: "relative" }}>
        <input
          type="range" min={min} max={max} step={step} value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          style={{
            width: "100%",
            background: `linear-gradient(to right, var(--accent) ${pct}%, var(--surface) ${pct}%)`,
          }}
          className="accent"
        />
      </div>
    </div>
  );
}

function Toggle({ label, value, onChange }: {
  label: string; value: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      marginBottom: 8, cursor: "pointer", gap: 8,
    }}>
      <span style={{ color: "var(--text-secondary)", fontSize: 11, flex: 1 }}>{label}</span>
      <div
        onClick={() => onChange(!value)}
        style={{
          width: 28, height: 16, borderRadius: 8,
          background: value ? "var(--accent)" : "var(--surface)",
          border: `1px solid ${value ? "var(--accent)" : "var(--border-mid)"}`,
          position: "relative",
          transition: "background 150ms var(--ease), border-color 150ms var(--ease)",
          flexShrink: 0,
        }}
      >
        <div style={{
          position: "absolute", top: 2,
          left: value ? 14 : 2,
          width: 10, height: 10, borderRadius: "50%",
          background: "#fff",
          transition: "left 150ms var(--ease)",
        }} />
      </div>
    </label>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      color: "var(--text-muted)", fontSize: 10, fontWeight: 600,
      letterSpacing: "0.08em", textTransform: "uppercase",
      marginBottom: 10, paddingBottom: 6,
      borderBottom: "1px solid var(--border)",
    }}>
      {children}
    </div>
  );
}

function Section({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{
      background: "var(--surface)",
      borderRadius: "var(--radius-md)",
      border: "1px solid var(--border)",
      padding: "10px 12px",
      marginBottom: 6,
    }}>
      <SectionLabel>{title}</SectionLabel>
      {children}
    </div>
  );
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const nodes         = useStore((s) => s.nodes);
  const environment   = useStore((s) => s.environment);
  const economics     = useStore((s) => s.economics);
  const selectedNodeId = useStore((s) => s.selectedNodeId);
  const result        = useStore((s) => s.result);
  const configHash    = useStore((s) => s.configHash);
  const simulating    = useStore((s) => s.simulating);
  const recalculating = useStore((s) => s.recalculating);
  const error         = useStore((s) => s.error);

  const setEnvironment  = useStore((s) => s.setEnvironment);
  const setEconomics    = useStore((s) => s.setEconomics);
  const updateNode      = useStore((s) => s.updateNode);
  const removeNode      = useStore((s) => s.removeNode);
  const setResult       = useStore((s) => s.setResult);
  const setSimulating   = useStore((s) => s.setSimulating);
  const setRecalculating = useStore((s) => s.setRecalculating);
  const setError        = useStore((s) => s.setError);

  const selectedNode = nodes.find((n) => n.id === selectedNodeId);
  const econTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [simHover, setSimHover] = useState(false);

  const handleSimulate = useCallback(async () => {
    setSimulating(true);
    setError(null);
    try {
      const data = await runSimulation({ nodes, environment, economics });
      setResult(data);
    } catch (e: any) {
      setError(e.message ?? "Simulation error");
    } finally {
      setSimulating(false);
    }
  }, [nodes, environment, economics, setResult, setSimulating, setError]);

  const handleEconChange = useCallback((patch: Partial<EconomicsSpec>) => {
    setEconomics(patch);
    if (!configHash) return;
    if (econTimerRef.current) clearTimeout(econTimerRef.current);
    econTimerRef.current = setTimeout(async () => {
      setRecalculating(true);
      try {
        const merged = { ...economics, ...patch };
        const data = await recalcEconomics({ config_hash: configHash, economics: merged });
        if (result) setResult({ ...result, economics: data.economics, viable_vs_retail: data.viable_vs_retail });
      } catch { /* non-critical */ } finally {
        setRecalculating(false);
      }
    }, 150);
  }, [configHash, economics, result, setEconomics, setResult, setRecalculating]);

  return (
    <div style={{
      width: 264,
      background: "var(--panel)",
      borderLeft: "1px solid var(--border)",
      overflowY: "auto",
      padding: "10px 10px 20px",
      display: "flex",
      flexDirection: "column",
      gap: 0,
      flexShrink: 0,
    }}>

      {/* Run button */}
      <button
        onClick={handleSimulate}
        disabled={simulating}
        onMouseEnter={() => setSimHover(true)}
        onMouseLeave={() => setSimHover(false)}
        style={{
          width: "100%", marginBottom: 8,
          padding: "8px 0",
          background: simulating ? "rgba(254,109,126,0.4)" : simHover ? "var(--accent-hover)" : "var(--accent)",
          color: "#fff",
          border: "none",
          borderRadius: "var(--radius)",
          fontWeight: 600, fontSize: 12,
          letterSpacing: "-0.01em",
          cursor: simulating ? "not-allowed" : "pointer",
          transition: "background 120ms var(--ease)",
          fontFamily: "inherit",
        }}
      >
        {simulating ? "Simulating…" : "Run Simulation"}
      </button>

      {error && (
        <div style={{
          background: "rgba(248,113,113,0.08)",
          border: "1px solid rgba(248,113,113,0.25)",
          color: "var(--red)",
          padding: "6px 8px", borderRadius: "var(--radius)",
          fontSize: 11, marginBottom: 8, lineHeight: 1.4,
        }}>
          {error}
        </div>
      )}

      {/* Selected node */}
      {selectedNode && (
        <Section title={<>Node · <span style={{ color: "var(--accent)", fontWeight: 700 }}>{selectedNode.id}</span></>}>
          {/* Type badge */}
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 5,
            padding: "2px 7px", borderRadius: 99, marginBottom: 10,
            border: `1px solid ${selectedNode.has_grid_port
              ? "rgba(254,109,126,0.35)"
              : "var(--border-mid)"}`,
            background: selectedNode.has_grid_port
              ? "rgba(254,109,126,0.08)"
              : "rgba(255,255,255,0.04)",
            fontSize: 10, color: selectedNode.has_grid_port ? "var(--red)" : "var(--text-muted)",
            textTransform: "capitalize",
          }}>
            {selectedNode.has_grid_port ? "⚡ Grid Port / VSC" : selectedNode.node_type.replace(/_/g, " ")}
          </div>

          {selectedNode.has_grid_port ? (
            /* Grid meter node — controls the utility connection */
            <>
              <div style={{
                fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5, marginBottom: 10,
              }}>
                This is the VSC node — it interfaces the DC microgrid with the utility grid.
                It sets the DC bus voltage reference and handles import/export.
                No local load or generation is modelled here; adjust physics through the simulation.
              </div>
              <Toggle label="Has grid port" value={selectedNode.has_grid_port}
                onChange={(v) => updateNode(selectedNode.id, { has_grid_port: v })} />
              <Slider label="Rooftop solar (kW)" value={selectedNode.pv_kw} min={0} max={20} step={0.5} unit=" kW"
                onChange={(v) => updateNode(selectedNode.id, { pv_kw: v })} />
            </>
          ) : (
            /* Regular prosumer node */
            <>
              <Slider label="Solar (kW)" value={selectedNode.pv_kw} min={0} max={50} step={0.5} unit=" kW"
                onChange={(v) => updateNode(selectedNode.id, { pv_kw: v })} />
              <Slider label="Battery (kWh)" value={selectedNode.battery_kwh} min={0} max={100} step={1} unit=" kWh"
                onChange={(v) => updateNode(selectedNode.id, { battery_kwh: v })} />
              <Slider label="Avg Load (kW)" value={selectedNode.avg_load_kw} min={0} max={10} step={0.1} unit=" kW"
                onChange={(v) => updateNode(selectedNode.id, { avg_load_kw: v })} />
              <Slider label="Genset (kW)" value={selectedNode.generator_kw} min={0} max={20} step={0.5} unit=" kW"
                onChange={(v) => updateNode(selectedNode.id, { generator_kw: v })} />
              <Toggle label="Grid port" value={selectedNode.has_grid_port}
                onChange={(v) => updateNode(selectedNode.id, { has_grid_port: v })} />
              <Toggle label="Deferrable load" value={selectedNode.is_deferrable}
                onChange={(v) => updateNode(selectedNode.id, { is_deferrable: v })} />
            </>
          )}

          <button
            onClick={() => removeNode(selectedNode.id)}
            style={{
              width: "100%", marginTop: 6, padding: "5px 0",
              background: "transparent",
              color: "var(--red)", border: "1px solid rgba(248,113,113,0.20)",
              borderRadius: "var(--radius)", cursor: "pointer",
              fontSize: 11, fontFamily: "inherit",
              transition: "background 120ms var(--ease)",
            }}
          >
            Remove node
          </button>
        </Section>
      )}

      {/* Environment */}
      <Section title="Environment">
        <Slider label="Irradiance" value={environment.irradiance_scale} min={0.3} max={1.2} step={0.05}
          onChange={(v) => setEnvironment({ irradiance_scale: v })} />
        <Slider label="Horizon" value={environment.horizon_hours} min={24} max={168} step={24} unit=" h"
          onChange={(v) => setEnvironment({ horizon_hours: v })} />
      </Section>

      {/* Economics */}
      <Section title={<>Economics{recalculating && <span style={{ marginLeft: 6, color: "var(--text-muted)", fontWeight: 400 }}>updating…</span>}</>}>
        <Slider label="Discom Retail" value={economics.discom_retail_rate} min={3} max={12} step={0.25} unit=" ₹/kWh"
          onChange={(v) => handleEconChange({ discom_retail_rate: v })} />
        <Slider label="Diesel" value={economics.diesel_cost_rs_per_kwh} min={10} max={40} step={1} unit=" ₹/kWh"
          onChange={(v) => handleEconChange({ diesel_cost_rs_per_kwh: v })} />
        <Slider label="P2P Price" value={economics.p2p_price_rs_per_kwh} min={1} max={10} step={0.25} unit=" ₹/kWh"
          onChange={(v) => handleEconChange({ p2p_price_rs_per_kwh: v })} />
        <Slider label="Carbon Price" value={economics.carbon_price_rs_per_tonne} min={0} max={2000} step={50} unit=" ₹/t"
          onChange={(v) => handleEconChange({ carbon_price_rs_per_tonne: v })} />
        <Slider label="VOLL" value={economics.voll_rs_per_kwh} min={5} max={100} step={1} unit=" ₹/kWh"
          onChange={(v) => handleEconChange({ voll_rs_per_kwh: v })} />
        <Slider label="Discount Rate" value={Math.round(economics.discount_rate * 100)} min={5} max={20} step={1} unit="%"
          onChange={(v) => handleEconChange({ discount_rate: v / 100 })} />
        <Slider label="BESS VGF" value={Math.round(economics.bess_vgf_frac * 100)} min={0} max={40} step={5} unit="%"
          onChange={(v) => handleEconChange({ bess_vgf_frac: v / 100 })} />
        <div style={{ marginTop: 4 }}>
          <Toggle label="PM Surya Ghar" value={economics.pm_surya_ghar}
            onChange={(v) => handleEconChange({ pm_surya_ghar: v })} />
          <Toggle label="KUSUM FLS" value={economics.kusum_fls}
            onChange={(v) => handleEconChange({ kusum_fls: v })} />
        </div>
      </Section>
    </div>
  );
}
