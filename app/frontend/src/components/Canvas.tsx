import React, { useCallback, useRef, useState } from "react";
import { Stage, Layer, Circle, Text, Line, Rect, Arrow } from "react-konva";
import { useStore } from "../store";
import type { NodeSpec, TimeSeriesPoint, TopologyEdge } from "../store";

// ── Node appearance ───────────────────────────────────────────────────────────

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
  if (n.has_grid_port)                            return NODE_COLOR.grid_meter;
  if (n.pv_kw >= 10)                              return NODE_COLOR.solar_farm;
  if (n.generator_kw > 0 && n.avg_load_kw < 0.1) return NODE_COLOR.genset;
  return NODE_COLOR[n.node_type] ?? "#64748b";
}

function nodeIcon(n: NodeSpec) {
  if (n.has_grid_port)                            return NODE_ICON.grid_meter;
  if (n.pv_kw >= 10)                              return NODE_ICON.solar_farm;
  if (n.generator_kw > 0 && n.avg_load_kw < 0.1) return NODE_ICON.genset;
  return NODE_ICON[n.node_type] ?? "•";
}

function nodeRadius(n: NodeSpec) {
  return 20 + Math.min(n.pv_kw / 6, 8) + Math.min(n.battery_kwh / 12, 6);
}

// ── Voltage colour ────────────────────────────────────────────────────────────

function vmColor(vm: number | undefined): string {
  if (vm === undefined || isNaN(vm)) return "rgba(255,255,255,0.25)";
  if (vm >= 0.95 && vm <= 1.05) return "#34d399";
  if (vm >= 0.90 && vm <= 1.10) return "#fbbf24";
  return "#f87171";
}

// ── Cable colour ──────────────────────────────────────────────────────────────

function lineColor(loading: number | undefined, islanded: boolean, hasData: boolean): string {
  if (islanded) return "#f87171";
  if (!hasData)               return "rgba(255,255,255,0.35)";
  if (loading === undefined)  return "rgba(255,255,255,0.30)";
  if (loading > 90) return "#f87171";
  if (loading > 60) return "#fbbf24";
  if (loading > 5)  return "rgba(96,165,250,0.80)";
  return "rgba(255,255,255,0.25)";
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function Canvas() {
  const nodes          = useStore((s) => s.nodes);
  const selected       = useStore((s) => s.selectedNodeId);
  const result         = useStore((s) => s.result);
  const step           = useStore((s) => s.playbackStep);
  const customEdges    = useStore((s) => s.customEdges);
  const moveNode       = useStore((s) => s.moveNode);
  const selectNode     = useStore((s) => s.selectNode);
  const addNode        = useStore((s) => s.addNode);
  const addCustomEdge  = useStore((s) => s.addCustomEdge);
  const removeCustomEdge = useStore((s) => s.removeCustomEdge);

  const stageRef = useRef<any>(null);
  const point    = result?.timeseries?.[step];
  const topology = result?.topology ?? [];

  // Connect-mode state
  const [connectMode, setConnectMode]     = useState(false);
  const [wireSource, setWireSource]       = useState<string | null>(null);
  const [cursorPos, setCursorPos]         = useState<{ x: number; y: number } | null>(null);

  // Build physics topology (T1_STAR always)
  const knownEdgePairs: [string, string, number][] = [
    ["M", "S",  0.040],
    ["M", "H1", 0.030],
    ["M", "H2", 0.060],
    ["M", "H3", 0.090],
    ["H1","H2", 0.035],
    ["H2","H3", 0.045],
  ];
  const nodeIds = new Set(nodes.map((n) => n.id));
  const hubId = nodes.find((n) => n.has_grid_port)?.id ?? nodes[0]?.id ?? "M";
  const fallbackEdges: TopologyEdge[] = [
    ...knownEdgePairs
      .filter(([a, b]) => nodeIds.has(a) && nodeIds.has(b))
      .map(([a, b, lkm]) => ({ name: `${a}-${b}`, from_node: a, to_node: b, length_km: lkm })),
    ...nodes
      .filter((n) => !["M","S","H1","H2","H3"].includes(n.id) && n.id !== hubId)
      .map((n) => ({ name: `${hubId}-${n.id}`, from_node: hubId, to_node: n.id, length_km: 0.06 })),
  ];
  const physicsEdges = topology.length > 0 ? topology : fallbackEdges;

  // All visible edges: physics + custom (deduplicated)
  const physicsEdgeNames = new Set(physicsEdges.map((e) => e.name));
  const extraCustom = customEdges.filter((e) => !physicsEdgeNames.has(e.name));

  // Node position lookup
  const nodePos: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) nodePos[n.id] = { x: n.x, y: n.y };

  // Drop handler
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const type = e.dataTransfer.getData("node-type");
    if (!type || !stageRef.current) return;
    const box = stageRef.current.container().getBoundingClientRect();
    addNode(type, e.clientX - box.left, e.clientY - box.top - 32);
  }, [addNode]);

  // Mouse move for wire-in-progress ghost line
  const handleMouseMove = useCallback((e: any) => {
    if (!connectMode || !wireSource) return;
    const pos = e.target.getStage()?.getPointerPosition();
    if (pos) setCursorPos(pos);
  }, [connectMode, wireSource]);

  // Node click handler (works in both normal and connect modes)
  const handleNodeClick = useCallback((nodeId: string) => {
    if (connectMode) {
      if (!wireSource) {
        setWireSource(nodeId);           // pick source
      } else if (wireSource !== nodeId) {
        addCustomEdge(wireSource, nodeId); // complete wire
        setWireSource(null);
        setConnectMode(false);
        setCursorPos(null);
      }
    } else {
      selectNode(selected === nodeId ? null : nodeId);
    }
  }, [connectMode, wireSource, addCustomEdge, selectNode, selected]);

  const exitConnect = useCallback(() => {
    setConnectMode(false);
    setWireSource(null);
    setCursorPos(null);
  }, []);

  return (
    <div
      style={{ flex: 1, background: "#111111", position: "relative", overflow: "hidden" }}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
      onKeyDown={(e) => e.key === "Escape" && exitConnect()}
      tabIndex={-1}
    >
      {/* ── Status / toolbar bar ── */}
      <div style={{
        position: "absolute", top: 0, left: 0, right: 0, height: 32,
        background: "rgba(10,10,10,0.85)", backdropFilter: "blur(8px)",
        borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center",
        padding: "0 10px", gap: 10, zIndex: 10,
      }}>
        <span style={{ color: "var(--text-muted)", fontSize: 11, fontWeight: 500, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          Microgrid
        </span>

        {/* Connect-wire button */}
        {!connectMode ? (
          <button
            onClick={() => { setConnectMode(true); setWireSource(null); selectNode(null); }}
            style={{
              padding: "2px 9px", fontSize: 10, fontWeight: 500,
              background: "rgba(255,255,255,0.06)",
              border: "1px solid var(--border-mid)",
              borderRadius: "var(--radius)", color: "var(--text-secondary)",
              cursor: "pointer", fontFamily: "inherit",
              transition: "background 120ms",
            }}
          >
            Connect ↗
          </button>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{
              padding: "2px 9px", fontSize: 10, fontWeight: 600,
              background: "rgba(251,191,36,0.12)",
              border: "1px solid rgba(251,191,36,0.35)",
              borderRadius: "var(--radius)", color: "#fbbf24",
            }}>
              {wireSource ? `From ${wireSource} → click destination` : "Click source node"}
            </span>
            <button
              onClick={exitConnect}
              style={{
                padding: "2px 8px", fontSize: 10,
                background: "transparent",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)", color: "var(--text-muted)",
                cursor: "pointer", fontFamily: "inherit",
              }}
            >
              Cancel
            </button>
          </div>
        )}

        {/* Simulation status pills */}
        {point && !connectMode && (
          <>
            <StatusPill label={point.islanded ? "Islanded" : "Grid-tied"}
              color={point.islanded ? "var(--amber)" : "var(--green)"} />
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
        )}

        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 10 }}>
          {connectMode ? "Esc to cancel" : "Drag to reposition · click to select"}
        </span>
      </div>

      <Stage
        ref={stageRef}
        width={900} height={560}
        style={{ marginTop: 32, cursor: connectMode ? "crosshair" : "default" }}
        onMouseMove={handleMouseMove}
        onClick={(e) => {
          // Click on empty canvas while in connect mode → cancel
          if (connectMode && e.target === e.target.getStage()) exitConnect();
        }}
      >
        <Layer>
          {/* Dot grid */}
          {Array.from({ length: 23 }).flatMap((_, xi) =>
            Array.from({ length: 14 }).map((_, yi) => (
              <Circle key={`d-${xi}-${yi}`} x={xi * 40 + 20} y={yi * 40 + 20}
                radius={1} fill="rgba(255,255,255,0.05)"
                listening={false} perfectDrawEnabled={false} />
            ))
          )}

          {/* ── Physics DC cables ── */}
          {physicsEdges.map((edge) => {
            const from = nodePos[edge.from_node];
            const to   = nodePos[edge.to_node];
            if (!from || !to) return null;
            return <CableEdge key={edge.name} edge={edge} from={from} to={to}
              point={point} isCustom={false} onRemove={null} />;
          })}

          {/* ── Custom user-drawn cables (dashed, removable) ── */}
          {extraCustom.map((edge) => {
            const from = nodePos[edge.from_node];
            const to   = nodePos[edge.to_node];
            if (!from || !to) return null;
            return <CableEdge key={edge.name} edge={edge} from={from} to={to}
              point={point} isCustom={true} onRemove={() => removeCustomEdge(edge.name)} />;
          })}

          {/* ── Ghost wire while connecting ── */}
          {connectMode && wireSource && cursorPos && nodePos[wireSource] && (
            <Line
              points={[nodePos[wireSource].x, nodePos[wireSource].y, cursorPos.x, cursorPos.y]}
              stroke="rgba(251,191,36,0.6)" strokeWidth={1.5}
              dash={[6, 4]} listening={false} perfectDrawEnabled={false}
            />
          )}

          {/* ── Nodes ── */}
          {nodes.map((n) => {
            const r     = nodeRadius(n);
            const color = nodeColor(n);
            const sel   = n.id === selected;
            const isSrc = n.id === wireSource;
            const nd    = point?.node_data?.[n.id];
            const soc   = nd?.soc_pct;
            const vm    = point?.vm_pu?.[n.id];
            const vmCol = vmColor(vm);

            return (
              <React.Fragment key={n.id}>
                {/* Selection ring */}
                {sel && !connectMode && (
                  <Circle x={n.x} y={n.y} radius={r + 7}
                    stroke="rgba(254,109,126,0.4)" strokeWidth={1.5}
                    listening={false} perfectDrawEnabled={false} />
                )}
                {/* Wire-source ring */}
                {isSrc && (
                  <Circle x={n.x} y={n.y} radius={r + 7}
                    stroke="rgba(251,191,36,0.8)" strokeWidth={2}
                    listening={false} perfectDrawEnabled={false} />
                )}
                {/* Voltage ring */}
                {vm !== undefined && (
                  <Circle x={n.x} y={n.y} radius={r + 2}
                    stroke={vmCol} strokeWidth={1.5} opacity={0.55}
                    listening={false} perfectDrawEnabled={false} />
                )}
                {/* Main node body */}
                <Circle
                  x={n.x} y={n.y} radius={r}
                  fill={color} opacity={connectMode && !isSrc ? 0.65 : (sel ? 1 : 0.82)}
                  stroke={isSrc ? "rgba(251,191,36,0.9)"
                    : sel ? "rgba(254,109,126,0.8)"
                    : "rgba(255,255,255,0.15)"}
                  strokeWidth={isSrc || sel ? 1.5 : 0.5}
                  draggable={!connectMode}
                  perfectDrawEnabled={false}
                  onDragEnd={(e) => moveNode(n.id, e.target.x(), e.target.y())}
                  onClick={() => handleNodeClick(n.id)}
                  onTap={() => handleNodeClick(n.id)}
                  style={{ cursor: connectMode ? "crosshair" : "pointer" }}
                />
                {/* Icon */}
                <Text x={n.x - 8} y={n.y - 8} text={nodeIcon(n)} fontSize={15}
                  listening={false} perfectDrawEnabled={false} />
                {/* Node label */}
                <Text x={n.x - 20} y={n.y + r + 5} text={n.id}
                  fontSize={10} fill="rgba(255,255,255,0.45)" width={40} align="center"
                  listening={false} perfectDrawEnabled={false} />
                {/* Voltage label */}
                {vm !== undefined && (
                  <Text x={n.x - 22} y={n.y + r + 16}
                    text={`${vm.toFixed(3)} pu`}
                    fontSize={8} fill={vmCol} opacity={0.85}
                    width={44} align="center"
                    listening={false} perfectDrawEnabled={false} />
                )}
                {/* SoC bar */}
                {soc !== undefined && n.battery_kwh > 0 && (
                  <>
                    <Rect x={n.x - 12} y={n.y - r - 8} width={24} height={3}
                      fill="rgba(255,255,255,0.10)" cornerRadius={1.5}
                      listening={false} perfectDrawEnabled={false} />
                    <Rect x={n.x - 12} y={n.y - r - 8} width={24 * (soc / 100)} height={3}
                      fill={soc > 50 ? "#34d399" : soc > 20 ? "#fbbf24" : "#f87171"}
                      cornerRadius={1.5}
                      listening={false} perfectDrawEnabled={false} />
                  </>
                )}
              </React.Fragment>
            );
          })}
        </Layer>
      </Stage>

      {/* Legend */}
      {result && (
        <div style={{
          position: "absolute", bottom: 10, left: 14,
          display: "flex", gap: 14, alignItems: "center",
        }}>
          <LegendItem color="#34d399" label="0.95–1.05 pu" />
          <LegendItem color="#fbbf24" label="±10% voltage" />
          <LegendItem color="#f87171" label="Critical" />
          <LegendItem color="rgba(96,165,250,0.8)" label="Active flow" />
          <LegendItem color="rgba(255,255,255,0.35)" dashed label="Custom wire" />
        </div>
      )}
    </div>
  );
}

// ── Cable Edge sub-component ──────────────────────────────────────────────────

function CableEdge({
  edge, from, to, point, isCustom, onRemove,
}: {
  edge: TopologyEdge;
  from: { x: number; y: number };
  to:   { x: number; y: number };
  point: TimeSeriesPoint | undefined;
  isCustom: boolean;
  onRemove: (() => void) | null;
}) {
  const flowKw   = point?.line_flows_kw?.[edge.name] ?? 0;
  const loading  = point?.line_loading_pct?.[edge.name];
  const islanded = point?.islanded ?? false;
  const lColor   = isCustom
    ? "rgba(251,191,36,0.55)"
    : lineColor(loading, islanded, !!point);
  const absFlow  = Math.abs(flowKw);
  const hasFlow  = absFlow > 0.05 && !isCustom;
  const strokeW  = hasFlow ? Math.min(1 + absFlow / 5, 4) : (isCustom ? 1.2 : 1);

  const fwd = flowKw >= 0;
  const ax  = fwd ? to.x   : from.x;
  const ay  = fwd ? to.y   : from.y;
  const bx  = fwd ? from.x : to.x;
  const by  = fwd ? from.y : to.y;

  const mx  = (from.x + to.x) / 2;
  const my  = (from.y + to.y) / 2;

  return (
    <React.Fragment>
      {/* Cable line */}
      <Line
        points={[from.x, from.y, to.x, to.y]}
        stroke={lColor}
        strokeWidth={strokeW}
        dash={isCustom ? [6, 5] : (islanded ? [5, 4] : undefined)}
        listening={false} perfectDrawEnabled={false}
      />

      {/* Directional arrow */}
      {hasFlow && (
        <Arrow
          points={[bx + (ax - bx) * 0.35, by + (ay - by) * 0.35,
                   bx + (ax - bx) * 0.65, by + (ay - by) * 0.65]}
          pointerLength={7} pointerWidth={6}
          fill={lColor} stroke={lColor} strokeWidth={strokeW}
          listening={false} perfectDrawEnabled={false}
        />
      )}

      {/* Flow label */}
      {hasFlow && (
        <Text x={mx - 18} y={my - 9} text={`${absFlow.toFixed(1)}kW`}
          fontSize={8.5} fill={lColor} width={36} align="center"
          listening={false} perfectDrawEnabled={false} />
      )}

      {/* Loading % */}
      {!isCustom && loading !== undefined && loading > 5 && (
        <Text x={mx - 18} y={my + 1} text={`${loading.toFixed(0)}%`}
          fontSize={7} fill="rgba(255,255,255,0.28)" width={36} align="center"
          listening={false} perfectDrawEnabled={false} />
      )}

      {/* Custom label + ✕ hit area */}
      {isCustom && (
        <>
          <Text x={mx - 18} y={my - 8} text="custom"
            fontSize={7.5} fill="rgba(251,191,36,0.6)" width={36} align="center"
            listening={false} perfectDrawEnabled={false} />
          {onRemove && (
            <Text
              x={mx - 6} y={my + 2} text="✕"
              fontSize={8} fill="rgba(251,191,36,0.7)"
              width={12} align="center"
              onClick={onRemove}
              onTap={onRemove}
              style={{ cursor: "pointer" }}
            />
          )}
        </>
      )}
    </React.Fragment>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function StatusPill({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "2px 7px", borderRadius: 99,
      border: `1px solid ${color}40`, background: `${color}14`,
    }}>
      <span style={{ width: 5, height: 5, borderRadius: "50%", background: color, flexShrink: 0 }} />
      <span style={{ color, fontSize: 10, fontWeight: 500 }}>{label}</span>
    </span>
  );
}

function LegendItem({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
      <svg width={20} height={4}>
        {dashed
          ? <line x1={0} y1={2} x2={20} y2={2} stroke={color} strokeWidth={1.5} strokeDasharray="4 3" />
          : <line x1={0} y1={2} x2={20} y2={2} stroke={color} strokeWidth={2} />}
      </svg>
      <span style={{ color: "var(--text-muted)", fontSize: 9 }}>{label}</span>
    </div>
  );
}
