import React, { useEffect, useState } from "react";
import { fetchStates } from "../api";
import { useStore } from "../store";

// ── Types ─────────────────────────────────────────────────────────────────────

interface StateMeta {
  domestic_tariff: number;
  commercial_tariff: number;
  avg_outage_rural_h: number;
  ghi_kwh_per_m2_day: number;
  voll_rs_per_kwh: number;
  state_subsidy_note: string;
  pitch_notes: string;
  tariff_order_ref: string;
}

interface StatePreset {
  code: string;
  name: string;
  discom: string;
  region: string;
  economics: Record<string, unknown>;
  environment: { irradiance_scale: number; outage_windows: number[][] };
  meta: StateMeta;
}

// ── Region colours (subtle) ───────────────────────────────────────────────────

const REGION_TINT: Record<string, string> = {
  "East India":        "#60a5fa",
  "North India (Hills)": "#a78bfa",
  "West India":        "#fb923c",
  "Central India":     "#fbbf24",
  "South India":       "#34d399",
};

function regionColor(region: string) {
  return REGION_TINT[region] ?? "#94a3b8";
}

// ── Main component ────────────────────────────────────────────────────────────

export default function StatePresets() {
  const [states, setStates] = useState<Record<string, StatePreset>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const setEconomics   = useStore((s) => s.setEconomics);
  const setEnvironment = useStore((s) => s.setEnvironment);

  useEffect(() => {
    fetchStates()
      .then(setStates)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  function applyState(code: string) {
    const s = states[code];
    if (!s) return;
    setSelected(code);
    setEconomics(s.economics as any);
    setEnvironment({
      irradiance_scale: s.environment.irradiance_scale,
      outage_windows: s.environment.outage_windows,
    });
  }

  if (loading) return null;

  const activeState = selected ? states[selected] : null;

  return (
    <div style={{
      background: "var(--panel)",
      borderBottom: "1px solid var(--border)",
      padding: "8px 14px",
    }}>
      {/* State pills row */}
      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
        <span style={{
          color: "var(--text-muted)", fontSize: 10, fontWeight: 600,
          letterSpacing: "0.08em", textTransform: "uppercase",
          marginRight: 4, flexShrink: 0,
        }}>
          State
        </span>

        {Object.values(states).map((s) => {
          const active = selected === s.code;
          const col = regionColor(s.region);
          return (
            <button
              key={s.code}
              onClick={() => applyState(s.code)}
              title={`${s.name} · ${s.discom}\nOutage: ${s.meta.avg_outage_rural_h}h/day · GHI: ${s.meta.ghi_kwh_per_m2_day} kWh/m²/day`}
              style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "3px 9px",
                borderRadius: "var(--radius)",
                border: `1px solid ${active ? col + "60" : "var(--border)"}`,
                background: active ? col + "18" : "var(--surface)",
                cursor: "pointer",
                transition: "all 120ms var(--ease)",
                fontFamily: "inherit",
              }}
            >
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: col, flexShrink: 0 }} />
              <span style={{
                color: active ? col : "var(--text-secondary)",
                fontSize: 11, fontWeight: active ? 600 : 400,
              }}>
                {s.code}
              </span>
            </button>
          );
        })}

        {selected && (
          <button
            onClick={() => setSelected(null)}
            style={{
              marginLeft: 4, padding: "3px 7px",
              background: "transparent", border: "1px solid var(--border)",
              borderRadius: "var(--radius)", color: "var(--text-muted)",
              fontSize: 10, cursor: "pointer", fontFamily: "inherit",
            }}
          >
            clear
          </button>
        )}
      </div>

      {/* Inline detail strip when a state is selected */}
      {activeState && (
        <div style={{
          display: "flex", gap: 16, marginTop: 7,
          paddingTop: 7, borderTop: "1px solid var(--border)",
          flexWrap: "wrap",
        }}>
          <Detail label="DISCOM"   value={activeState.discom} />
          <Detail label="Retail"   value={`₹${activeState.meta.domestic_tariff}/kWh`} />
          <Detail label="Outage"   value={`${activeState.meta.avg_outage_rural_h}h/day`}
            color={activeState.meta.avg_outage_rural_h > 5 ? "var(--red)" : activeState.meta.avg_outage_rural_h > 2 ? "var(--amber)" : "var(--green)"} />
          <Detail label="GHI"      value={`${activeState.meta.ghi_kwh_per_m2_day} kWh/m²`} />
          <Detail label="VOLL"     value={`₹${activeState.meta.voll_rs_per_kwh}/kWh`} />
          <Detail label="Ref"      value={activeState.meta.tariff_order_ref} muted />
        </div>
      )}
    </div>
  );
}

function Detail({ label, value, color, muted }: {
  label: string; value: string; color?: string; muted?: boolean;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <span style={{ color: "var(--text-muted)", fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase" }}>
        {label}
      </span>
      <span style={{ color: color ?? (muted ? "var(--text-muted)" : "var(--text)"), fontSize: 11, fontWeight: muted ? 400 : 500 }}>
        {value}
      </span>
    </div>
  );
}
