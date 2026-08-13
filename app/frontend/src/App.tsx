import Palette from "./components/Palette";
import Canvas from "./components/Canvas";
import Sidebar from "./components/Sidebar";
import ResultsPanel from "./components/ResultsPanel";
import StatePresets from "./components/StatePresets";

export default function App() {
  return (
    <div style={{
      display: "flex",
      height: "100vh",
      width: "100vw",
      background: "var(--bg)",
      overflow: "hidden",
    }}>
      <ResultsPanel />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <Palette />
        <StatePresets />
        <Canvas />
      </div>
      <Sidebar />
    </div>
  );
}
