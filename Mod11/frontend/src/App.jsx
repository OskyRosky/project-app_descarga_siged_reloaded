// Importamos los hooks de React y el CSS principal
import { useEffect, useRef, useState } from "react";
import "./App.css";

/* ======================================================
   🔧 CONFIGURACIÓN DEL API BACKEND
   ------------------------------------------------------
   - Si definimos VITE_API_BASE, la usamos (p.ej. http://127.0.0.1:8200)
   - Si no existe, usamos el mismo origen donde se sirva el frontend.
====================================================== */
const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : path);

/* ======================================================
   ✅ VALIDACIÓN DE URL DEL SIGED
   ------------------------------------------------------
   Verifica:
   - http/https
   - dominio exacto: cgrweb.cgr.go.cr
   - ruta que empiece con /apex/f
   - que el query/hash contenga p=CORRESPONDENCIA:1 y P1_CONSECUTIVO (32 hex)
====================================================== */
function isValidSigedUrl(raw) {
  try {
    const u = new URL(raw.trim());
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    if (u.hostname.toLowerCase() !== "cgrweb.cgr.go.cr") return false;

    const pathOk = u.pathname.toLowerCase().startsWith("/apex/f");
    const bag = (u.search || u.hash || "").toUpperCase();
    const hasCorr = /P=CORRESPONDENCIA:1/.test(bag);
    // P1_CONSECUTIVO:<32 hex>
    const m = bag.match(/P1_CONSECUTIVO:([0-9A-F]{32})/);
    return pathOk && hasCorr && !!m;
  } catch {
    return false;
  }
}

/* ======================================================
   📝 Utilidad: agrega consejo al final de mensajes
====================================================== */
function withAdvice(msg) {
  return `${msg} Por favor verifique de nuevo el enlace para poder descargar los archivos y vuelva a intentarlo.`;
}

/* ======================================================
   🎯 COMPONENTE PRINCIPAL
====================================================== */
export default function App() {
  // --- ESTADOS PRINCIPALES ---
  const [url, setUrl] = useState("");             // URL ingresada por el usuario
  const [status, setStatus] = useState("inicio"); // inicio | iniciando… | descargando | finalizado | error | cancelado
  const [total, setTotal] = useState(0);
  const [done, setDone] = useState(0);
  const [percent, setPercent] = useState(0);
  const [lastFile, setLastFile] = useState("");
  const [msg, setMsg] = useState("");

  // Control del polling
  const pollingRef = useRef(null);

  /* ======================================================
     ⏱️ CONTROL DEL POLLING
  ====================================================== */
  function stopPolling() {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }
  function startPolling() {
    stopPolling();
    pollingRef.current = setInterval(fetchProgreso, 800);
  }

  /* ======================================================
     📡 CONSULTAR PROGRESO (para polling durante la descarga)
     - Solo se usa cuando ya sabemos que hay una descarga en curso.
  ====================================================== */
  async function fetchProgreso() {
    try {
      const res = await fetch(api("/progreso"));
      const data = await res.json();

      setTotal(data.total ?? 0);
      setDone(data.done ?? 0);
      setPercent(data.percent ?? 0);
      setLastFile(data.last_file || "");

      if (data.status === "error") {
        setStatus("error");
        // Si es un error típico de enlace o de "no hay links", añadimos el consejo.
        const base = data.last_error || "Error en la descarga.";
        const needsAdvice =
          /no se encontraron enlaces/i.test(base) ||
          /url inválida|dominio no permitido/i.test(base);
        setMsg(needsAdvice ? withAdvice(base) : base);
        stopPolling();
        return;
      }
      if (data.status === "cancelado") {
        setStatus("cancelado");
        stopPolling();
        return;
      }
      if (data.status === "finalizado" || (data.percent ?? 0) >= 100) {
        setStatus("finalizado");
        stopPolling();
        return;
      }
      if (data.status === "descargando") {
        setStatus("descargando");
        return;
      }
      // Si el backend reporta "inicio" en pleno polling, mantenemos estado actual.
    } catch {
      setStatus("error");
      setMsg("⚠️ Error consultando progreso");
      stopPolling();
    }
  }

  /* ======================================================
     🚀 INICIAR DESCARGA (submit del formulario)
  ====================================================== */
  async function handleDownload(e) {
    e.preventDefault();
    setMsg("");

    // Validación estricta en frontend con consejo
    if (!isValidSigedUrl(url)) {
      setStatus("error");
      setMsg(
        withAdvice(
          "URL inválida. Debe ser de cgrweb.cgr.go.cr con CORRESPONDENCIA:1 y P1_CONSECUTIVO."
        )
      );
      return;
    }

    // Reset visual antes de iniciar
    setStatus("iniciando…");
    setTotal(0);
    setDone(0);
    setPercent(0);
    setLastFile("");

    try {
      const res = await fetch(api("/descargar"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const data = await res.json();

      if (res.ok && data.ok) {
        setStatus("descargando");
        startPolling(); // aquí sí empezamos a consultar /progreso
      } else {
        // Si el backend respondió con 400/404/409, mostramos detalle y consejo.
        const base = data.detail || data.message || "No se pudo iniciar la descarga.";
        const needsAdvice =
          res.status === 400 || res.status === 404 ||
          /no se encontraron enlaces/i.test(base) ||
          /url inválida|dominio no permitido/i.test(base);
        setStatus("error");
        setMsg(needsAdvice ? withAdvice(base) : base);
      }
    } catch (err) {
      setStatus("error");
      setMsg(`Error al iniciar: ${String(err)}`);
    }
  }

  /* ======================================================
     🔁 LIMPIAR / REINICIAR
  ====================================================== */
  function handleReset() {
    stopPolling();
    setStatus("inicio");
    setTotal(0);
    setDone(0);
    setPercent(0);
    setLastFile("");
    setUrl("");
    setMsg("");
  }

  /* ======================================================
     🧩 AL MONTAR LA PÁGINA
     ------------------------------------------------------
     - Queremos PANTALLA LIMPIA siempre.
     - Solo si el backend está en "descargando", retomamos el avance.
     - Si está en "finalizado"/"error"/"inicio", ignoramos y mantenemos UI limpia.
  ====================================================== */
  useEffect(() => {
    let aborted = false;

    (async () => {
      try {
        const res = await fetch(api("/progreso"));
        const data = await res.json();
        if (aborted) return;

        if (data.status === "descargando") {
          // Hay una descarga viva → retomamos UI + polling
          setStatus("descargando");
          setTotal(data.total ?? 0);
          setDone(data.done ?? 0);
          setPercent(data.percent ?? 0);
          setLastFile(data.last_file || "");
          startPolling();
        } else {
          // Cualquier otro estado → pantalla limpia
          handleReset();
        }
      } catch {
        // Si falla la consulta inicial, dejamos la UI limpia
        handleReset();
      }
    })();

    return () => {
      aborted = true;
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Mostrar botón “Limpiar” SOLAMENTE cuando la descarga ya terminó
  const showCompleted = percent >= 100 || status === "finalizado";
  const inputDisabled = status === "descargando" || status === "iniciando…";

  /* ======================================================
     💅 RENDER
  ====================================================== */
  return (
    <div className="container">
      <h1>Módulo de descarga de documentos en el SIGED.</h1>

      <form onSubmit={handleDownload}>
        <label>🔗 URL del SIGED:</label><br />
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://cgrweb.cgr.go.cr/apex/f?p=CORRESPONDENCIA:1:...P1_CONSECUTIVO:XXXXXXXX"
          required
          disabled={inputDisabled}
        /><br />

        <p style={{ fontStyle: "italic", color: "gray" }}>
          📁 Los archivos se guardarán en la carpeta de descarga, bajo la carpeta <strong>SIGED_DOCUMENTOS</strong>.
        </p>

        <button type="submit" disabled={inputDisabled}>
          Iniciar Descarga
        </button>

        {/* Botón LIMPIAR aparece solo al finalizar */}
        {showCompleted && (
          <>
            {" "}
            <button type="button" onClick={handleReset}>
              Limpiar
            </button>
          </>
        )}
      </form>

      <div className="progress-section">
        <p><strong>Estado:</strong> {status}</p>
        {msg && <p style={{ color: "#ffb3b3" }}>{msg}</p>}

        <p><strong>Total de documentos:</strong> {total}</p>
        <p><strong>Progreso:</strong> {done}/{total} ({percent}%)</p>

        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${Math.min(100, percent)}%` }} />
        </div>

        {lastFile ? (
          <p style={{ marginTop: 8 }}>📄 Último archivo: <em>{lastFile}</em></p>
        ) : null}

        {showCompleted && (
          <p style={{ marginTop: 8 }}>
            ✅ {total} documentos descargados en <strong>SIGED_DOCUMENTOS</strong>.
          </p>
        )}
      </div>
    </div>
  );
}