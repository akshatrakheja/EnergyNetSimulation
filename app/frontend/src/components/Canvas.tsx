import React, { useCallback, useRef, useState } from "react";
import { Stage, Layer, Circle, Text, Line, Rect } from "react-konva";
import { useStore } from "../store";
import type { NodeSpec, TimeSeriesPoint } from "../store";

const NODE_COLORS: Record<string, string> = {
  residential: "#4CAF50",
  apartment: "#66BB6A",
  cold_storage: "#29B6F6",
  shop: "#FFA726",
  pump: "#8D6E63",
  telecom_tower: "#AB47BC",
  solar_farm: "#FFD54F",
  grid_meter: "#EF5350",
  genset: "#78909C",
};

const NODE_ICONS: Record<string, string> = {
  residential: "🏠",
  apartment: "🏢",
  cold_storage: "❄️",
  shop: "🏪",
  pump: "💧",
  telecom_tower: "📡",
  solar_farm: "☀️",
  grid_meter: "⚡",
  genset: "🔋",
};

function getNodeColor(n: NodeSpec) {
  if (n.has_grid_port) return NODE_COLORS.grid_meter;
  if (n.pv_kw >= 10) return NODE_COLORS.solar_farm;
  if (n.generator_kw > 0 && n.avg_load_kw === 0) return NODE_COLORS.genset;
  return NODE_COLORS[n.node_type] ?? "#999";
}

function getNodeIcon(n: NodeSpec) {
  if (n.has_grid_port) return NODE_ICONS.grid_meter;
  if (n.pv_kw >= 10) return NODE_ICONS.solar_farm;
  if (n.generator_kw > 0 && n.avg_load_kw === 0) return NODE_ICONS.genset;
  return NODE_ICONS[n.node_type] ?? "•";
}

function getNodeRadius(n: NodeSpec) {
  const base = 24;
  const pvBonus = Math.min(n.pv_kw / 5, 8);
  const battBonus = Math.min(n.battery_kwh / 10, 6);
  return base + pvBonus + battBonus;
}

function getFlowData(point: TimeSeriesPoint | undefined, nodeId: string) {
  if (!point?.node_data?.[nodeId]) return null;
  return point.node_data[nodeId];
}

export default function Canvas() {
  const nodes = useStore((s) => s.nodes);
  const selectedNodeId = useStore((s) => s.selectedNodeId);
  const result = useStore((s) => s.result);
  const playbackStep = useStore((s) => s.playbackStep);
  const moveNode = useStore((s) => s.moveNode);
  const selectNode = useStore((s) => s.selectNode);
  const addNode = useStore((s) => s.addNode);
  const removeNode = useStore((s) => s.removeNode);

  const [dragType, setDragType] = useState<string | null>(null);
  const stageRef = useRef<any>(null);

  const currentPoint = result?.timeseries?.[playbackStep];

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData("node-type");
      if (!type) return;
      const stage = stageRef.current;
      if (!stage) return;
      const stageBox = stage.container().getBoundingClientRect();
      const x = e.clientX - stageBox.left;
      const y = e.clientY - stageBox.top;
      addNode(type, x, y);
    },
    [addNode]
  );

  // Find grid node (M) as hub for star topology lines
  const gridNode = nodes.find((n) => n.has_grid_port) ?? nodes[0];

  return (
    <div
      style={{ flex: 1, background: "#1a1a2e", position: "relative", minHeight: 500 }}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
    >
      {/* Header bar */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 36,
          background: "rgba(0,0,0,0.4)",
          display: "flex",
          alignItems: "center",
          padding: "0 12px",
          zIndex: 10,
          gap: 8,
        }}
      >
        <span style={{ color: "#aaa", fontSize: 12, fontWeight: 600 }}>
          MICROGRID CANVAS
        </span>
        {currentPoint && (
          <span style={{ color: "#7ecfff", fontSize: 12, marginLeft: "auto" }}>
            {currentPoint.islanded ? "🔴 ISLANDED" : "🟢 GRID"} &nbsp;|&nbsp;
            Grid: {currentPoint.grid_exchange_kw.toFixed(1)} kW &nbsp;|&nbsp;
            Shed: {currentPoint.shed_load_kw.toFixed(1)} kW
          </span>
        )}
      </div>

      <Stage
        ref={stageRef}
        width={800}
        height={500}
        style={{ marginTop: 36 }}
      >
        <Layer>
          {/* Grid background */}
          {Array.from({ length: 20 }).map((_, i) => (
            <Line
              key={`gx${i}`}
              points={[i * 50, 0, i * 50, 500]}
              stroke="#1f2044"
              strokeWidth={1}
            />
          ))}
          {Array.from({ length: 12 }).map((_, i) => (
            <Line
              key={`gy${i}`}
              points={[0, i * 50, 800, i * 50]}
              stroke="#1f2044"
              strokeWidth={1}
            />
          ))}

          {/* DC cables (star from grid node to all others) */}
          {gridNode &&
            nodes
              .filter((n) => n.id !== gridNode.id)
              .map((n) => {
                const flow = getFlowData(currentPoint, n.id);
                const hasLoad = flow && Math.abs(flow.load_kw ?? 0) > 0.01;
                const lineColor = currentPoint?.islanded
                  ? "#ef5350"
                  : hasLoad
                  ? "#66bb6a"
                  : "#3a3a5c";
                return (
                  <Line
                    key={`cable-${n.id}`}
                    points={[gridNode.x, gridNode.y, n.x, n.y]}
                    stroke={lineColor}
                    strokeWidth={hasLoad ? 2.5 : 1.5}
                    dash={currentPoint?.islanded ? [6, 4] : undefined}
                    opacity={0.7}
                  />
                );
              })}

          {/* Nodes */}
          {nodes.map((n) => {
            const r = getNodeRadius(n);
            const color = getNodeColor(n);
            const selected = n.id === selectedNodeId;
            const flow = getFlowData(currentPoint, n.id);
            const soc = flow?.soc_pct;

            return (
              <React.Fragment key={n.id}>
                {/* Glow ring for selected */}
                {selected && (
                  <Circle
                    x={n.x}
                    y={n.y}
                    radius={r + 6}
                    stroke="#FFD700"
                    strokeWidth={2}
                    dash={[4, 3]}
                  />
                )}

                {/* Main circle */}
                <Circle
                  x={n.x}
                  y={n.y}
                  radius={r}
                  fill={color}
                  opacity={0.9}
                  stroke={selected ? "#FFD700" : "#fff"}
                  strokeWidth={selected ? 2 : 1}
                  shadowColor={color}
                  shadowBlur={selected ? 15 : 5}
                  shadowOpacity={0.6}
                  draggable
                  onDragEnd={(e) => {
                    moveNode(n.id, e.target.x(), e.target.y());
                  }}
                  onClick={() => selectNode(n.id)}
                  onTap={() => selectNode(n.id)}
                />

                {/* Icon */}
                <Text
                  x={n.x - 8}
                  y={n.y - 8}
                  text={getNodeIcon(n)}
                  fontSize={16}
                  listening={false}
                />

                {/* Label */}
                <Text
                  x={n.x - 20}
                  y={n.y + r + 4}
                  text={n.id}
                  fontSize={11}
                  fill="#ccc"
                  width={40}
                  align="center"
                  listening={false}
                />

                {/* SoC gauge bar */}
                {soc !== undefined && n.battery_kwh > 0 && (
                  <>
                    <Rect
                      x={n.x - 14}
                      y={n.y - r - 10}
                      width={28}
                      height={5}
                      fill="#333"
                      cornerRadius={2}
                    />
                    <Rect
                      x={n.x - 14}
                      y={n.y - r - 10}
                      width={28 * (soc / 100)}
                      height={5}
                      fill={soc > 50 ? "#4caf50" : soc > 20 ? "#ffc107" : "#ef5350"}
                      cornerRadius={2}
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
