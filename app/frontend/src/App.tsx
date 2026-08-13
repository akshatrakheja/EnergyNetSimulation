import Palette from "./components/Palette";
import Canvas from "./components/Canvas";
import Sidebar from "./components/Sidebar";
import ResultsPanel from "./components/ResultsPanel";

export default function App() {
  return (
    <div style={styles.root}>
      {/* Left: Results */}
      <ResultsPanel />

      {/* Center: Canvas + Palette */}
      <div style={styles.center}>
        <Palette />
        <Canvas />
      </div>

      {/* Right: Controls */}
      <Sidebar />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: "flex",
    height: "100vh",
    width: "100vw",
    background: "#0a0a23",
    fontFamily: "'Inter', -apple-system, sans-serif",
    color: "#eee",
    overflow: "hidden",
  },
  center: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
  },
};
