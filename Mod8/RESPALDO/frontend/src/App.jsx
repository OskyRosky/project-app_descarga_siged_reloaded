import { useEffect, useRef, useState } from "react";
import "./App.css";

export default function App() {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState(""); // "inicio" | "🟡 Descarga en curso…" | "finalizado" | mensaje de error
  const [total, setTotal] = useState(0);
  const [done, setDone] = useState(0);
  const [percent, setPercent] = useState(0);
  const [lastFile, setLastFile] = useState("");
  const pollingRef = useRef(null);

  async function fetchProgreso() {
    try {
      const res = await fetch("/progreso");
      const data = await res.json();
      setStatus(data.status || "");
      setTotal(data.total ?? 0);
      setDone(data.done ?? 0);
      setPercent(data.percent ?? 0);
      setLastFile(data.last_file || "");

      // si ya llegó a 100, frenamos el polling
      if ((data.percent ?? 0) >= 100 || data.status === "done" || data.status === "finalizado") {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
        // opcional: normalizar etiqueta de estado
        setStatus("finalizado");
      }
    } catch {
      setStatus("⚠️ Error al consultar el progreso");
    }
  }

  async function handleDownload(e) {
    e.preventDefault();
    if (!url || !url.startsWith("http")) {
      setStatus("⚠️ Ingrese una URL válida (http/https)");
      return;
    }
    // estado inicial de una ejecución
    setStatus("iniciando…");
    setTotal(0);
    setDone(0);
    setPercent(0);
    setLastFile("");

    try {
      const res = await fetch("/descargar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Error en descarga");

      // arranca polling
      if (pollingRef.current) clearInterval(pollingRef.current);
      pollingRef.current = setInterval(fetchProgreso, 500);
      setStatus("🟡 Descarga en curso…");
    } catch (err) {
      setStatus(`⚠️ Error al iniciar la descarga: ${String(err)}`);
    }
  }

  function handleReset() {
    // detener cualquier polling previo
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    // volver a estado “inicio”
    setStatus("inicio");
    setTotal(0);
    setDone(0);
    setPercent(0);
    setLastFile("");
    setUrl("");
  }

  useEffect(() => {
    // al cargar, intenta leer progreso (por si ya había algo en curso)
    fetchProgreso();
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const showCompleted = percent >= 100 || status === "finalizado";

  return (
    <div className="container">
      <h1>Módulo de descarga de documentos en el SIGED.</h1>

      <form onSubmit={handleDownload}>
        <label>🔗 URL del SIGED:</label><br />
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          placeholder="https://..."
        /><br />

        <p style={{ fontStyle: "italic", color: "gray" }}>
          📁 Los archivos se guardarán en la carpeta de descarga, bajo la carpeta <strong>SIGED_DOCUMENTOS</strong>.
        </p><br />

        <button type="submit">Iniciar Descarga</button>
      </form>

      <div className="progress-section">
        <p><strong>Estado:</strong> {status || "inicio"}</p>
        <p><strong>Total de documentos:</strong> {total}</p>
        <p><strong>Progreso:</strong> {done}/{total} ({percent}%)</p>

        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{ width: `${Math.min(100, percent)}%` }}
          />
        </div>

        {lastFile ? (
          <p style={{ marginTop: 8 }}>📄 Último archivo: <em>{lastFile}</em></p>
        ) : null}

        {showCompleted && (
          <>
            <p style={{ marginTop: 8 }}>
              ✅ {total} documentos descargados en <strong>SIGED_DOCUMENTOS</strong>.
            </p>
            <button onClick={handleReset} style={{ marginTop: 10 }}>
              Iniciar otra descarga
            </button>
          </>
        )}
      </div>
    </div>
  );
}