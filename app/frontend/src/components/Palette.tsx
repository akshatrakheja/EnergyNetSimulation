import React from "react";

const ENTITY_TYPES = [
  { type: "residential", icon: "🏠", label: "House" },
  { type: "apartment", icon: "🏢", label: "Apartment" },
  { type: "cold_storage", icon: "❄️", label: "Cold Store" },
  { type: "shop", icon: "🏪", label: "Shop" },
  { type: "pump", icon: "💧", label: "Pump" },
  { type: "telecom_tower", icon: "📡", label: "Tower" },
  { type: "solar_farm", icon: "☀️", label: "Solar Farm" },
  { type: "grid_meter", icon: "⚡", label: "Grid Meter" },
  { type: "genset", icon: "🔋", label: "Genset" },
];

export default function Palette() {
  return (
    <div style={styles.palette}>
      <div style={styles.title}>ENTITIES</div>
      <div style={styles.grid}>
        {ENTITY_TYPES.map((e) => (
          <div
            key={e.type}
            draggable
            onDragStart={(ev) => {
              ev.dataTransfer.setData("node-type", e.type);
            }}
            style={styles.item}
            title={`Drag to add a ${e.label}`}
          >
            <span style={styles.icon}>{e.icon}</span>
            <span style={styles.label}>{e.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  palette: {
    background: "#16213e",
    borderBottom: "1px solid #2a2a4a",
    padding: "8px 12px",
  },
  title: {
    color: "#888",
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: 1.5,
    marginBottom: 6,
  },
  grid: {
    display: "flex",
    gap: 6,
    flexWrap: "wrap",
  },
  item: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    padding: "6px 8px",
    background: "#1a1a3e",
    border: "1px solid #2a2a5a",
    borderRadius: 6,
    cursor: "grab",
    minWidth: 56,
    transition: "border-color 0.15s",
  },
  icon: {
    fontSize: 20,
  },
  label: {
    color: "#aaa",
    fontSize: 9,
    marginTop: 2,
  },
};
