const API = "http://localhost:8080";

export async function runSimulation(body: unknown) {
  const res = await fetch(`${API}/api/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Simulation failed (${res.status}): ${text}`);
  }
  return res.json();
}

export async function recalcEconomics(body: unknown) {
  const res = await fetch(`${API}/api/economics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Econ recalc failed (${res.status}): ${text}`);
  }
  return res.json();
}

export async function fetchPresets() {
  const res = await fetch(`${API}/api/presets`);
  if (!res.ok) throw new Error("Failed to fetch presets");
  return res.json();
}
