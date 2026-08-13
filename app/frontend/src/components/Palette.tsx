import React, { useState } from "react";

const ENTITIES = [
  { type: "residential",   icon: "🏠", label: "House"      },
  { type: "apartment",     icon: "🏢", label: "Apartment"  },
  { type: "cold_storage",  icon: "❄️", label: "Cold Store" },
  { type: "shop",          icon: "🏪", label: "Shop"       },
  { type: "pump",          icon: "💧", label: "Pump"       },
  { type: "telecom_tower", icon: "📡", label: "Tower"      },
  { type: "solar_farm",    icon: "☀️", label: "Solar Farm" },
  { type: "grid_meter",    icon: "⚡", label: "Grid"       },
  { type: "genset",        icon: "🔋", label: "Genset"     },
];

export default function Palette() {
  const [hovered, setHovered] = useState<string | null>(null);

  return (
    <div style={{
      background: "var(--panel)",
      borderBottom: "1px solid var(--border)",
      padding: "8px 14px",
      display: "flex",
      alignItems: "center",
      gap: 6,
    }}>
      <span style={{
        color: "var(--text-muted)",
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        marginRight: 6,
        flexShrink: 0,
      }}>
        Entities
      </span>

      {ENTITIES.map((e) => (
        <div
          key={e.type}
          draggable
          onDragStart={(ev) => ev.dataTransfer.setData("node-type", e.type)}
          onMouseEnter={() => setHovered(e.type)}
          onMouseLeave={() => setHovered(null)}
          title={`Drag to place a ${e.label}`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 5,
            padding: "4px 9px",
            borderRadius: "var(--radius)",
            border: `1px solid ${hovered === e.type ? "var(--border-mid)" : "var(--border)"}`,
            background: hovered === e.type ? "var(--surface-hover)" : "var(--surface)",
            cursor: "grab",
            transition: "background 120ms var(--ease), border-color 120ms var(--ease)",
            userSelect: "none",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ fontSize: 13, lineHeight: 1 }}>{e.icon}</span>
          <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>{e.label}</span>
        </div>
      ))}
    </div>
  );
}
