import { create } from "zustand";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface NodeSpec {
  id: string;
  node_type: string;
  pv_kw: number;
  battery_kwh: number;
  avg_load_kw: number;
  has_grid_port: boolean;
  generator_kw: number;
  gen_fuel_type: string;
  is_deferrable: boolean;
  critical_load_kw: number;
  x: number;
  y: number;
}

export interface EnvironmentSpec {
  outage_windows: number[][];
  irradiance_scale: number;
  horizon_hours: number;
  dt_min: number;
}

export interface EconomicsSpec {
  diesel_cost_rs_per_kwh: number;
  p2p_price_rs_per_kwh: number;
  p2p_transaction_charge_rs_per_kwh: number;
  wheeling_charge_rs_per_kwh: number;
  carbon_price_rs_per_tonne: number;
  voll_rs_per_kwh: number;
  discount_rate: number;
  project_life_years: number;
  discom_retail_rate: number;
  pm_surya_ghar: boolean;
  kusum_fls: boolean;
  bess_vgf_frac: number;
  rural_license_exempt: boolean;
  solar_rs_per_kw: number;
  bess_rs_per_kwh: number;
}

export interface TimeSeriesPoint {
  t: string;
  grid_exchange_kw: number;
  shed_load_kw: number;
  curtailed_kw: number;
  islanded: boolean;
  node_data: Record<string, Record<string, number>>;
}

export interface SimResult {
  config_hash: string;
  sim_seconds: number;
  metrics: Record<string, unknown>;
  economics: Record<string, unknown>;
  timeseries: TimeSeriesPoint[];
  viable_vs_retail: "green" | "amber" | "red";
}

// ---------------------------------------------------------------------------
// Default values
// ---------------------------------------------------------------------------

const NODE_TEMPLATES: Record<string, Partial<NodeSpec>> = {
  residential: { pv_kw: 0, battery_kwh: 10, avg_load_kw: 1.5, node_type: "residential" },
  apartment: { pv_kw: 0, battery_kwh: 5, avg_load_kw: 2.0, node_type: "apartment" },
  cold_storage: { pv_kw: 0, battery_kwh: 0, avg_load_kw: 2.0, node_type: "cold_storage" },
  shop: { pv_kw: 0, battery_kwh: 5, avg_load_kw: 1.0, node_type: "shop" },
  pump: { pv_kw: 0, battery_kwh: 0, avg_load_kw: 2.2, node_type: "pump", is_deferrable: true },
  telecom_tower: { pv_kw: 0, battery_kwh: 20, avg_load_kw: 2.5, node_type: "telecom_tower" },
  solar_farm: { pv_kw: 20, battery_kwh: 50, avg_load_kw: 0, node_type: "cold_storage", has_grid_port: false },
  grid_meter: { pv_kw: 0, battery_kwh: 0, avg_load_kw: 0, node_type: "residential", has_grid_port: true },
  genset: { pv_kw: 0, battery_kwh: 0, avg_load_kw: 0, generator_kw: 5, node_type: "residential" },
};

export { NODE_TEMPLATES };

const DEFAULT_ENV: EnvironmentSpec = {
  outage_windows: [[19, 21], [6, 7]],
  irradiance_scale: 0.85,
  horizon_hours: 24,
  dt_min: 15,
};

const DEFAULT_ECON: EconomicsSpec = {
  diesel_cost_rs_per_kwh: 22,
  p2p_price_rs_per_kwh: 4.5,
  p2p_transaction_charge_rs_per_kwh: 0,
  wheeling_charge_rs_per_kwh: 0,
  carbon_price_rs_per_tonne: 400,
  voll_rs_per_kwh: 22,
  discount_rate: 0.10,
  project_life_years: 25,
  discom_retail_rate: 6.5,
  pm_surya_ghar: true,
  kusum_fls: true,
  bess_vgf_frac: 0.30,
  rural_license_exempt: true,
  solar_rs_per_kw: 45000,
  bess_rs_per_kwh: 10000,
};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface AppState {
  nodes: NodeSpec[];
  environment: EnvironmentSpec;
  economics: EconomicsSpec;
  selectedNodeId: string | null;

  result: SimResult | null;
  configHash: string | null;
  simulating: boolean;
  recalculating: boolean;
  error: string | null;

  // Canvas
  playbackStep: number;

  // Actions
  addNode: (type: string, x: number, y: number) => void;
  removeNode: (id: string) => void;
  updateNode: (id: string, patch: Partial<NodeSpec>) => void;
  moveNode: (id: string, x: number, y: number) => void;
  selectNode: (id: string | null) => void;
  setEnvironment: (patch: Partial<EnvironmentSpec>) => void;
  setEconomics: (patch: Partial<EconomicsSpec>) => void;
  setResult: (r: SimResult) => void;
  setSimulating: (v: boolean) => void;
  setRecalculating: (v: boolean) => void;
  setError: (e: string | null) => void;
  setPlaybackStep: (s: number) => void;
  loadPreset: (nodes: NodeSpec[], env: Partial<EnvironmentSpec>) => void;
}

let _nodeCounter = 0;

export const useStore = create<AppState>((set) => ({
  nodes: [
    { id: "M", node_type: "residential", pv_kw: 0, battery_kwh: 0, avg_load_kw: 0, has_grid_port: true, generator_kw: 0, gen_fuel_type: "diesel", is_deferrable: false, critical_load_kw: 0, x: 400, y: 300 },
    { id: "S", node_type: "cold_storage", pv_kw: 20, battery_kwh: 50, avg_load_kw: 2, has_grid_port: false, generator_kw: 0, gen_fuel_type: "diesel", is_deferrable: false, critical_load_kw: 1.5, x: 250, y: 150 },
    { id: "H1", node_type: "residential", pv_kw: 0, battery_kwh: 10, avg_load_kw: 1.5, has_grid_port: false, generator_kw: 4, gen_fuel_type: "diesel", is_deferrable: false, critical_load_kw: 0, x: 550, y: 150 },
    { id: "H2", node_type: "residential", pv_kw: 0, battery_kwh: 7, avg_load_kw: 2, has_grid_port: false, generator_kw: 5, gen_fuel_type: "diesel", is_deferrable: false, critical_load_kw: 0, x: 550, y: 350 },
    { id: "H3", node_type: "pump", pv_kw: 0, battery_kwh: 13.5, avg_load_kw: 2.2, has_grid_port: false, generator_kw: 6, gen_fuel_type: "diesel", is_deferrable: true, critical_load_kw: 0, x: 250, y: 350 },
  ],
  environment: { ...DEFAULT_ENV },
  economics: { ...DEFAULT_ECON },
  selectedNodeId: null,

  result: null,
  configHash: null,
  simulating: false,
  recalculating: false,
  error: null,
  playbackStep: 0,

  addNode: (type, x, y) =>
    set((s) => {
      _nodeCounter++;
      const tpl = NODE_TEMPLATES[type] ?? NODE_TEMPLATES.residential;
      const id = `${type.charAt(0).toUpperCase()}${_nodeCounter}`;
      const node: NodeSpec = {
        id,
        node_type: tpl.node_type ?? "residential",
        pv_kw: tpl.pv_kw ?? 0,
        battery_kwh: tpl.battery_kwh ?? 0,
        avg_load_kw: tpl.avg_load_kw ?? 1.5,
        has_grid_port: tpl.has_grid_port ?? false,
        generator_kw: tpl.generator_kw ?? 0,
        gen_fuel_type: "diesel",
        is_deferrable: tpl.is_deferrable ?? false,
        critical_load_kw: 0,
        x,
        y,
      };
      return { nodes: [...s.nodes, node] };
    }),

  removeNode: (id) =>
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== id),
      selectedNodeId: s.selectedNodeId === id ? null : s.selectedNodeId,
    })),

  updateNode: (id, patch) =>
    set((s) => ({
      nodes: s.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)),
    })),

  moveNode: (id, x, y) =>
    set((s) => ({
      nodes: s.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)),
    })),

  selectNode: (id) => set({ selectedNodeId: id }),

  setEnvironment: (patch) =>
    set((s) => ({ environment: { ...s.environment, ...patch } })),

  setEconomics: (patch) =>
    set((s) => ({ economics: { ...s.economics, ...patch } })),

  setResult: (r) => set({ result: r, configHash: r.config_hash }),
  setSimulating: (v) => set({ simulating: v }),
  setRecalculating: (v) => set({ recalculating: v }),
  setError: (e) => set({ error: e }),
  setPlaybackStep: (s) => set({ playbackStep: s }),
  loadPreset: (nodes, env) =>
    set((s) => ({
      nodes,
      environment: { ...s.environment, ...env },
      result: null,
      configHash: null,
    })),
}));
