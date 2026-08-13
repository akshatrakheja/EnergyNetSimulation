import React, { useCallback, useRef } from "react";
import { useStore } from "../store";
import { runSimulation, recalcEconomics } from "../api";
import type { EconomicsSpec, NodeSpec } from "../store";

// ---------------------------------------------------------------------------
// Slider helper
// ---------------------------------------------------------------------------

function Slider({
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  onChange: (v: number) => void;
}) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
        <span style={{ color: "#bbb", fontSize: 11 }}>{label}</span>
        <span style={{ color: "#7ecfff", fontSize: 11, fontWeight: 600 }}>
          {value}
          {unit ?? ""}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        style={{ width: "100%", accentColor: "#7ecfff" }}
      />
    </div>
  );
}

function Toggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginBottom: 8,
        color: "#bbb",
        fontSize: 11,
        cursor: "pointer",
      }}
    >
      {label}
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        style={{ accentColor: "#7ecfff" }}
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Sidebar() {
  const nodes = useStore((s) => s.nodes);
  const environment = useStore((s) => s.environment);
  const economics = useStore((s) => s.economics);
  const selectedNodeId = useStore((s) => s.selectedNodeId);
  const result = useStore((s) => s.result);
  const configHash = useStore((s) => s.configHash);
  const simulating = useStore((s) => s.simulating);
  const recalculating = useStore((s) => s.recalculating);
  const error = useStore((s) => s.error);

  const setEnvironment = useStore((s) => s.setEnvironment);
  const setEconomics = useStore((s) => s.setEconomics);
  const updateNode = useStore((s) => s.updateNode);
  const removeNode = useStore((s) => s.removeNode);
  const setResult = useStore((s) => s.setResult);
  const setSimulating = useStore((s) => s.setSimulating);
  const setRecalculating = useStore((s) => s.setRecalculating);
  const setError = useStore((s) => s.setError);

  const selectedNode = nodes.find((n) => n.id === selectedNodeId);

  const econTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSimulate = useCallback(async () => {
    setSimulating(true);
    setError(null);
    try {
      const body = { nodes, environment, economics };
      const data = await runSimulation(body);
      setResult(data);
    } catch (e: any) {
      setError(e.message ?? "Simulation error");
    } finally {
      setSimulating(false);
    }
  }, [nodes, environment, economics, setResult, setSimulating, setError]);

  const handleEconChange = useCallback(
    (patch: Partial<EconomicsSpec>) => {
      setEconomics(patch);
      if (!configHash) return;

      // Debounced recalc
      if (econTimerRef.current) clearTimeout(econTimerRef.current);
      econTimerRef.current = setTimeout(async () => {
        setRecalculating(true);
        try {
          const merged = { ...economics, ...patch };
          const data = await recalcEconomics({
            config_hash: configHash,
            economics: merged,
          });
          if (result) {
            setResult({
              ...result,
              economics: data.economics,
              viable_vs_retail: data.viable_vs_retail,
            });
          }
        } catch {
          // silent — econ recalc failures are non-critical
        } finally {
          setRecalculating(false);
        }
      }, 300);
    },
    [configHash, economics, result, setEconomics, setResult, setRecalculating]
  );

  return (
    <div style={styles.sidebar}>
      {/* Simulate button */}
      <button onClick={handleSimulate} disabled={simulating} style={styles.simBtn}>
        {simulating ? "⏳ Simulating..." : "▶ Run Simulation"}
      </button>

      {error && <div style={styles.error}>{error}</div>}

      {/* Selected node editor */}
      {selectedNode && (
        <Section title={`NODE: ${selectedNode.id}`}>
          <div style={{ fontSize: 11, color: "#888", marginBottom: 6 }}>
            Type: {selectedNode.node_type}
          </div>
          <Slider
            label="PV (kW)"
            value={selectedNode.pv_kw}
            min={0} max={50} step={0.5}
            unit=" kW"
            onChange={(v) => updateNode(selectedNode.id, { pv_kw: v })}
          />
          <Slider
            label="Battery (kWh)"
            value={selectedNode.battery_kwh}
            min={0} max={100} step={1}
            unit=" kWh"
            onChange={(v) => updateNode(selectedNode.id, { battery_kwh: v })}
          />
          <Slider
            label="Load (kW)"
            value={selectedNode.avg_load_kw}
            min={0} max={10} step={0.1}
            unit=" kW"
            onChange={(v) => updateNode(selectedNode.id, { avg_load_kw: v })}
          />
          <Slider
            label="Genset (kW)"
            value={selectedNode.generator_kw}
            min={0} max={20} step={0.5}
            unit=" kW"
            onChange={(v) => updateNode(selectedNode.id, { generator_kw: v })}
          />
          <Toggle
            label="Grid Port"
            value={selectedNode.has_grid_port}
            onChange={(v) => updateNode(selectedNode.id, { has_grid_port: v })}
          />
          <Toggle
            label="Deferrable"
            value={selectedNode.is_deferrable}
            onChange={(v) => updateNode(selectedNode.id, { is_deferrable: v })}
          />
          <button
            onClick={() => removeNode(selectedNode.id)}
            style={styles.deleteBtn}
          >
            Delete Node
          </button>
        </Section>
      )}

      {/* Environment */}
      <Section title="ENVIRONMENT">
        <Slider
          label="Irradiance"
          value={environment.irradiance_scale}
          min={0.3} max={1.2} step={0.05}
          onChange={(v) => setEnvironment({ irradiance_scale: v })}
        />
        <Slider
          label="Sim Hours"
          value={environment.horizon_hours}
          min={24} max={168} step={24}
          unit=" h"
          onChange={(v) => setEnvironment({ horizon_hours: v })}
        />
      </Section>

      {/* Economics */}
      <Section title={`ECONOMICS ${recalculating ? "⏳" : ""}`}>
        <Slider
          label="Discom Retail (₹/kWh)"
          value={economics.discom_retail_rate}
          min={3} max={12} step={0.25}
          unit=" ₹"
          onChange={(v) => handleEconChange({ discom_retail_rate: v })}
        />
        <Slider
          label="Diesel (₹/kWh)"
          value={economics.diesel_cost_rs_per_kwh}
          min={10} max={40} step={1}
          unit=" ₹"
          onChange={(v) => handleEconChange({ diesel_cost_rs_per_kwh: v })}
        />
        <Slider
          label="P2P Price (₹/kWh)"
          value={economics.p2p_price_rs_per_kwh}
          min={1} max={10} step={0.25}
          unit=" ₹"
          onChange={(v) => handleEconChange({ p2p_price_rs_per_kwh: v })}
        />
        <Slider
          label="Carbon Price (₹/t)"
          value={economics.carbon_price_rs_per_tonne}
          min={0} max={2000} step={50}
          unit=" ₹"
          onChange={(v) => handleEconChange({ carbon_price_rs_per_tonne: v })}
        />
        <Slider
          label="VOLL (₹/kWh)"
          value={economics.voll_rs_per_kwh}
          min={5} max={100} step={1}
          unit=" ₹"
          onChange={(v) => handleEconChange({ voll_rs_per_kwh: v })}
        />
        <Slider
          label="Discount Rate"
          value={Math.round(economics.discount_rate * 100)}
          min={5} max={20} step={1}
          unit="%"
          onChange={(v) => handleEconChange({ discount_rate: v / 100 })}
        />
        <Slider
          label="BESS VGF"
          value={Math.round(economics.bess_vgf_frac * 100)}
          min={0} max={40} step={5}
          unit="%"
          onChange={(v) => handleEconChange({ bess_vgf_frac: v / 100 })}
        />
        <Toggle
          label="PM Surya Ghar"
          value={economics.pm_surya_ghar}
          onChange={(v) => handleEconChange({ pm_surya_ghar: v })}
        />
        <Toggle
          label="KUSUM FLS"
          value={economics.kusum_fls}
          onChange={(v) => handleEconChange({ kusum_fls: v })}
        />
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section wrapper
// ---------------------------------------------------------------------------

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div style={styles.section}>
      <div style={styles.sectionTitle}>{title}</div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    width: 280,
    background: "#0f3460",
    borderLeft: "1px solid #1a1a4a",
    overflowY: "auto",
    padding: 12,
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  simBtn: {
    width: "100%",
    padding: "10px 0",
    background: "#e94560",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    fontWeight: 700,
    fontSize: 13,
    cursor: "pointer",
    marginBottom: 8,
    letterSpacing: 0.5,
  },
  error: {
    background: "#4a0020",
    color: "#ff8a8a",
    padding: "6px 8px",
    borderRadius: 4,
    fontSize: 11,
    marginBottom: 8,
    wordBreak: "break-word",
  },
  section: {
    background: "#16213e",
    borderRadius: 6,
    padding: "8px 10px",
    marginBottom: 6,
  },
  sectionTitle: {
    color: "#888",
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: 1.5,
    marginBottom: 8,
    borderBottom: "1px solid #2a2a4a",
    paddingBottom: 4,
  },
  deleteBtn: {
    width: "100%",
    padding: "6px 0",
    background: "#4a0020",
    color: "#ff8a8a",
    border: "1px solid #ef5350",
    borderRadius: 4,
    cursor: "pointer",
    fontSize: 11,
    marginTop: 6,
  },
};
