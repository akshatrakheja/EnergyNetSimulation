import React, { useCallback, useRef } from "react";
import { Stage, Layer, Circle, Text, Line, Rect } from "react-konva";
import { useStore } from "../store";
import type { NodeSpec, TimeSeriesPoint } from "../store";

// ── Node appearance ──────────────────────────────────────────────────────────

const NODE_COLOR: Record<string, string> = {
  residential:   "#60a5fa",
  apartment:     "#818cf8",
  cold_storage:  "#38bdf8",
  shop:          "#fb923c",
  pump:          "#a78bfa",
  telecom_tower: "#94a3b8",
  solar_farm:    "#fbbf24",
  grid_meter:    "#fe6d7e",
  genset:        "#6b7280",
};

const NODE_ICON: Record<string, string> = {
  residential:   "🏠",
  apartment:     "🏢",
  cold_storage:  "❄️",
  shop:          "🏪",
  pump:          "💧",
  telecom_tower: "📡",
  solar_farm:    "☀️",
  grid_meter:    "⚡",
  genset:        "🔋",
};

function nodeColor(n: NodeSpec) {
  if (n.has_grid_port)                         return NODE_COLOR.grid_meter;
  if (n.pv_kw >= 10)                           return NODE_COLOR.solar_farm;
  if (n.generator_kw > 0 && n.avg_load_kw < 0.1) return NODE_COLOR.genset;
  return NODE_COLOR[n.node_type] ?? "#64748b";
}

function nodeIcon(n: NodeSpec) {
  if (n.has_grid_port)                         return NODE_ICON.grid_meter;
  if (n.pv_kw >= 10)                           return NODE_ICON.solar_farm;
  if (n.generator_kw > 0 && n.avg_load_kw < 0.1) return NODE_ICON.genset;
  return NODE_ICON[n.node_type] ?? "•";
}

function nodeRadius(n: NodeSpec) {
  return 20 + Math.min(n.pv_kw / 6, 8) + Math.min(n.battery_kwh / 12, 6);
}

function getFlow(point: TimeSeriesPoint | undefined, id: string) {
  return point?.node_data?.[id] ?? null;
}

// ── Component ────────────────────────────────────────────────────────────────

export default function Canvas() {
  const nodes        = useStore((s) => s.nodes);
  const selected     = useStore((s) => s.selectedNodeId);
  const result       = useStore((s) => s.result);
  const step         = useStore((s) => s.playbackStep);
  const moveNode     = useStore((s) => s.moveNode);
  const selectNode   = useStore((s) => s.selectNode);
  const addNode      = useStore((s) => s.addNode);

  const stageRef = useRef<any>(null);
  const point    = result?.timeseries?.[step];
  const gridNode = nodes.find((n) => n.has_grid_port) ?? nodes[0];

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const type = e.dataTransfer.getData("node-type");
    if (!type || !stageRef.current) return;
    const box = stageRef.current.container().getBoundingClientRect();
    addNode(type, e.clientX - box.left, e.clientY - box.top);
  }, [addNode]);

  return (
    <div
      style={{ flex: 1, background: "#111111", position: "relative", overflow: "hidden" }}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
    >
      {/* Status bar */}
      <div style={{
        position: "absolute", top: 0, left: 0, right: 0, height: 32,
        background: "rgba(10,10,10,0.85)",
        backdropFilter: "blur(8px)",
        borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center",
        padding: "0 14px", gap: 16, zIndex: 10,
      }}>
        <span style={{ color: "var(--text-muted)", fontSize: 11, fontWeight: 500, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          Microgrid
        </span>

        {point ? (
          <>
            <StatusPill
              label={point.islanded ? "Islanded" : "Grid-tied"}
              color={point.islanded ? "var(--amber)" : "var(--green)"}
            />
            <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>
              Grid {point.grid_exchange_kw > 0 ? "import" : "export"}{" "}
              <b style={{ color: "var(--text)" }}>{Math.abs(point.grid_exchange_kw).toFixed(1)} kW</b>
            </span>
            {point.shed_load_kw > 0.01 && (
              <span style={{ color: "var(--red)", fontSize: 11 }}>
                Shed <b>{point.shed_load_kw.toFixed(1)} kW</b>
              </span>
            )}
          </>
        ) : (
          <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
            Drop entities · click to select · run simulation to animate
          </span>
        )}
      </div>

      <Stage ref={stageRef} width={900} height={560} style={{ marginTop: 32 }}>
        <Layer>
          {/* Dot grid */}
          {Array.from({ length: 30 }).flatMap((_, xi) =>
            Array.from({ length: 18 }).map((_, yi) => (
              <Circle
                key={`dot-${xi}-${yi}`}
                x={xi * 40 + 20} y={yi * 40 + 20}
                radius={1}
                fill="rgba(255,255,255,0.06)"
                listening={false}
                perfectDrawEnabled={false}
              />
            ))
          )}

          {/* DC cables */}
          {gridNode && nodes.filter((n) => n.id !== gridNode.id).map((n) => {
            const flow = getFlow(point, n.id);
            const active = flow ? Math.abs(flow.load_kw ?? 0) > 0.01 : false;
            return (
              <Line
                key={`cable-${n.id}`}
                points={[gridNode.x, gridNode.y, n.x, n.y]}
                stroke={point?.islanded ? "#f87171" : active ? "rgba(96,165,250,0.6)" : "rgba(255,255,255,0.08)"}
                strokeWidth={active ? 1.5 : 1}
                dash={point?.islanded ? [5, 4] : undefined}
                listening={false}
                perfectDrawEnabled={false}
              />
            );
          })}

          {/* Nodes */}
          {nodes.map((n) => {
            const r      = nodeRadius(n);
            const color  = nodeColor(n);
            const sel    = n.id === selected;
            const flow   = getFlow(point, n.id);
            const soc    = flow?.soc_pct;

            return (
              <React.Fragment key={n.id}>
                {/* Selection ring */}
                {sel && (
                  <Circle
                    x={n.x} y={n.y}
                    radius={r + 5}
                    stroke="rgba(254,109,126,0.5)"
                    strokeWidth={1.5}
                    listening={false}
                    perfectDrawEnabled={false}
                  />
                )}

                {/* Main circle */}
                <Circle
                  x={n.x} y={n.y}
                  radius={r}
                  fill={color}
                  opacity={sel ? 1 : 0.82}
                  stroke={sel ? "rgba(254,109,126,0.8)" : "rgba(255,255,255,0.15)"}
                  strokeWidth={sel ? 1.5 : 0.5}
                  draggable
                  perfectDrawEnabled={false}
                  onDragEnd={(e) => moveNode(n.id, e.target.x(), e.target.y())}
                  onClick={() => selectNode(sel ? null : n.id)}
                  onTap={() => selectNode(sel ? null : n.id)}
                />

                {/* Icon */}
                <Text
                  x={n.x - 8} y={n.y - 8}
                  text={nodeIcon(n)}
                  fontSize={15}
                  listening={false}
                  perfectDrawEnabled={false}
                />

                {/* Label */}
                <Text
                  x={n.x - 20} y={n.y + r + 5}
                  text={n.id}
                  fontSize={10}
                  fill="rgba(255,255,255,0.45)"
                  width={40} align="center"
                  listening={false}
                  perfectDrawEnabled={false}
                />

                {/* SoC bar */}
                {soc !== undefined && n.battery_kwh > 0 && (
                  <>
                    <Rect
                      x={n.x - 12} y={n.y - r - 8}
                      width={24} height={3}
                      fill="rgba(255,255,255,0.10)"
                      cornerRadius={1.5}
                      listening={false}
                      perfectDrawEnabled={false}
                    />
                    <Rect
                      x={n.x - 12} y={n.y - r - 8}
                      width={24 * (soc / 100)} height={3}
                      fill={soc > 50 ? "#34d399" : soc > 20 ? "#fbbf24" : "#f87171"}
                      cornerRadius={1.5}
                      listening={false}
                      perfectDrawEnabled={false}
                    />
                  </>
                )}
              </React.Fragment>
            );
          })}
        </Layer>
      </Stage>
    </div>
  );
}

function StatusPill({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "2px 7px",
      borderRadius: 99,
      border: `1px solid ${color}40`,
      background: `${color}14`,
    }}>
      <span style={{ width: 5, height: 5, borderRadius: "50%", background: color, flexShrink: 0 }} />
      <span style={{ color, fontSize: 10, fontWeight: 500 }}>{label}</span>
    </span>
  );
}
