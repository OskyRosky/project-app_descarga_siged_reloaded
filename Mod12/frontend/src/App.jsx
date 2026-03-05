// App.jsx

import { useEffect, useRef, useState } from "react";
import "./App.css";

/**
 * ============================================================
 *  CONFIG
 * ============================================================
 */
const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : path);

const SIGED_SUBFOLDER = "SIGED_DOCUMENTOS";

/**
 * ============================================================
 *  VALIDACIÓN URL (SIGED)
 * ============================================================
 */
function isValidSigedUrl(raw) {
  try {
    const u = new URL(raw.trim());
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    if (u.hostname.toLowerCase() !== "cgrweb.cgr.go.cr") return false;

    const pathOk = u.pathname.toLowerCase().startsWith("/apex/f");
    const bag = (u.search || u.hash || "").toUpperCase();
    const hasCorr = /P=CORRESPONDENCIA:1/.test(bag);
    const m = bag.match(/P1_CONSECUTIVO:([0-9A-F]{32})/);

    return pathOk && hasCorr && !!m;
  } catch {
    return false;
  }
}

function withAdvice(msg) {
  return `${msg} Por favor verifique de nuevo el enlace para poder descargar los archivos y vuelva a intentarlo.`;
}

/**
 * File System Access API (Chrome/Edge)
 */
function supportsDirectoryPicker() {
  return typeof window !== "undefined" && typeof window.showDirectoryPicker === "function";
}

/**
 * ============================================================
 *  APP
 * ============================================================
 */
export default function App() {
  /**
   * ------------------------------------------------------------
   * INPUT
   * ------------------------------------------------------------
   */
  const [url, setUrl] = useState("");

  /**
   * ------------------------------------------------------------
   * UI STATE
   * ------------------------------------------------------------
   * uiState: inicio | iniciando | descubriendo | descargando_cliente | finalizado | error | cancelado
   */
  const [uiState, setUiState] = useState("inicio");
  const [msg, setMsg] = useState("");

  /**
   * ------------------------------------------------------------
   * BACKEND SNAPSHOT (UN SOLO STATE)
   * ------------------------------------------------------------
   * status: inicio | descubriendo | esperando_descarga_cliente | error | cancelado | finalizado
   */
  const [snapshot, setSnapshot] = useState({
    status: "inicio",
    phase: "idle",
    total: 0,
    discovered_done: 0,
    client_done: 0,
    percent: 0,
    last_file: "",
    last_error: "",
    files_count: 0,
  });

  /**
   * Helper para aplicar snapshot con defaults seguros.
   * (Evita repetir setX(data.x ?? default) por todo lado)
   */
  function applySnapshot(data) {
    setSnapshot((prev) => ({
      ...prev,
      status: data.status ?? "inicio",
      phase: data.phase ?? "idle",
      total: data.total ?? 0,
      discovered_done: data.discovered_done ?? 0,
      client_done: data.client_done ?? 0,
      percent: data.percent ?? 0,
      last_file: data.last_file ?? "",
      last_error: data.last_error ?? "",
      files_count: data.files_count ?? 0,
    }));
  }

  /**
   * ------------------------------------------------------------
   * CARPETA DESTINO (CLIENT)
   * ------------------------------------------------------------
   */
  const [pickedFolderLabel, setPickedFolderLabel] = useState("");
  const folderHandleRef = useRef(null);
  const sigedDirHandleRef = useRef(null);

  /**
   * ------------------------------------------------------------
   * POLLING
   * ------------------------------------------------------------
   */
  const pollingRef = useRef(null);

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

  /**
   * ------------------------------------------------------------
   * GUARDAS: evitar doble corrida client-side
   * ------------------------------------------------------------
   */
  const clientRunRef = useRef({ running: false, startedForThisJob: false });

  /**
   * ============================================================
   *  FETCH /progreso
   * ============================================================
   */
  async function fetchProgreso() {
    try {
      const res = await fetch(api("/progreso"));
      const data = await res.json();

      // 1) aplicar snapshot
      applySnapshot(data);

      // 2) reglas de UI según backend
      if (data.status === "error") {
        setUiState("error");
        const base = data.last_error || "Error en la descarga.";
        const needsAdvice =
          /no se encontraron enlaces/i.test(base) ||
          /url inválida|dominio no permitido/i.test(base);
        setMsg(needsAdvice ? withAdvice(base) : base);
        stopPolling();
        return;
      }

      if (data.status === "cancelado") {
        setUiState("cancelado");
        stopPolling();
        return;
      }

      if (data.status === "finalizado") {
        setUiState("finalizado");
        stopPolling();
        return;
      }

      if (data.status === "descubriendo") {
        setUiState("descubriendo");
        return;
      }

      if (data.status === "esperando_descarga_cliente") {
        // seguimos polling durante descargas cliente
        return;
      }
    } catch {
      setUiState("error");
      setMsg("⚠️ Error consultando progreso");
      stopPolling();
    }
  }

  /**
   * ============================================================
   *  RESET (front + back)
   * ============================================================
   */
  async function handleReset() {
    stopPolling();
    clientRunRef.current = { running: false, startedForThisJob: false };

    try {
      await fetch(api("/reset"), { method: "POST" });
    } catch {
      // ignore
    }

    setUrl("");
    setUiState("inicio");
    setMsg("");

    // reset snapshot completo
    setSnapshot({
      status: "inicio",
      phase: "idle",
      total: 0,
      discovered_done: 0,
      client_done: 0,
      percent: 0,
      last_file: "",
      last_error: "",
      files_count: 0,
    });

    folderHandleRef.current = null;
    sigedDirHandleRef.current = null;
    setPickedFolderLabel("");
  }

  /**
   * ============================================================
   *  PICK FOLDER (opción 2)
   * ============================================================
   */
  async function pickFolder() {
    setMsg("");

    if (!supportsDirectoryPicker()) {
      setMsg(
        "Tu navegador no permite seleccionar carpeta (File System Access API). Usa Chrome/Edge para guardar en SIGED_DOCUMENTOS automáticamente."
      );
      return false;
    }

    try {
      const baseHandle = await window.showDirectoryPicker({ mode: "readwrite" });
      folderHandleRef.current = baseHandle;
      setPickedFolderLabel(baseHandle.name || "Carpeta seleccionada");

      const sigedDir = await baseHandle.getDirectoryHandle(SIGED_SUBFOLDER, { create: true });
      sigedDirHandleRef.current = sigedDir;

      return true;
    } catch (e) {
      setMsg(`No se seleccionó carpeta: ${String(e)}`);
      return false;
    }
  }

  /**
   * ============================================================
   *  START /descargar
   * ============================================================
   */
  async function handleStart(e) {
    e.preventDefault();
    setMsg("");

    if (!isValidSigedUrl(url)) {
      setUiState("error");
      setMsg(withAdvice("URL inválida. Debe ser de cgrweb.cgr.go.cr con CORRESPONDENCIA:1 y P1_CONSECUTIVO."));
      return;
    }

    // opción 2: pedir carpeta antes
    const okFolder = await pickFolder();
    if (!okFolder) return;

    setUiState("iniciando");

    // reset contadores para UI limpia (sin tocar backend)
    setSnapshot((prev) => ({
      ...prev,
      total: 0,
      discovered_done: 0,
      client_done: 0,
      percent: 0,
      last_file: "",
      last_error: "",
      files_count: 0,
    }));

    clientRunRef.current = { running: false, startedForThisJob: false };

    try {
      const res = await fetch(api("/descargar"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const data = await res.json();

      if (res.ok && data.ok) {
        setUiState("descubriendo");
        startPolling();
      } else {
        const base = data.detail || data.message || "No se pudo iniciar la descarga.";
        const needsAdvice =
          res.status === 400 ||
          res.status === 404 ||
          /no se encontraron enlaces/i.test(base) ||
          /url inválida|dominio no permitido/i.test(base);

        setUiState("error");
        setMsg(needsAdvice ? withAdvice(base) : base);
        stopPolling();
      }
    } catch (err) {
      setUiState("error");
      setMsg(`Error al iniciar: ${String(err)}`);
      stopPolling();
    }
  }

  /**
   * ============================================================
   *  DESCARGA 1 ARCHIVO (via /proxy) -> a SIGED_DOCUMENTOS
   * ============================================================
   */
  async function downloadOneFileViaProxy(fileObj) {
    const sigedDir = sigedDirHandleRef.current;
    if (!sigedDir) throw new Error("No hay carpeta SIGED_DOCUMENTOS seleccionada.");

    const fileName = fileObj.name || "archivo";
    const targetUrl = fileObj.url;

    const proxyUrl =
      api("/proxy") +
      `?url=${encodeURIComponent(targetUrl)}&name=${encodeURIComponent(fileName)}`;

    const resp = await fetch(proxyUrl);
    if (!resp.ok) {
      const t = await resp.text().catch(() => "");
      throw new Error(`Proxy HTTP ${resp.status}: ${t}`);
    }

    const fileHandle = await sigedDir.getFileHandle(fileName, { create: true });
    const writable = await fileHandle.createWritable();

    try {
      if (resp.body) {
        await resp.body.pipeTo(writable);
      } else {
        const blob = await resp.blob();
        await writable.write(blob);
        await writable.close();
      }
    } catch (e) {
      try {
        await writable.abort();
      } catch {}
      throw e;
    }
  }

  /**
   * ============================================================
   *  FALLBACK: descarga a "Downloads" del navegador
   * ============================================================
   */
  async function fallbackDownloadInBrowser(fileObj) {
    const fileName = fileObj.name || "archivo";
    const targetUrl = fileObj.url;

    const proxyUrl =
      api("/proxy") +
      `?url=${encodeURIComponent(targetUrl)}&name=${encodeURIComponent(fileName)}`;

    const resp = await fetch(proxyUrl);
    if (!resp.ok) throw new Error(`Proxy HTTP ${resp.status}`);

    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  /**
   * ============================================================
   *  RUN CLIENT DOWNLOADS (cuando backend está listo)
   * ============================================================
   */
  async function runClientDownloadsIfReady() {
    if (clientRunRef.current.running || clientRunRef.current.startedForThisJob) return;

    // condición de arranque
    if (snapshot.status !== "esperando_descarga_cliente" || snapshot.files_count <= 0) return;

    clientRunRef.current.running = true;
    clientRunRef.current.startedForThisJob = true;

    try {
      setUiState("descargando_cliente");
      setMsg("");

      const res = await fetch(api("/archivos"));
      const data = await res.json();
      const files = data.files || [];

      if (!Array.isArray(files) || files.length === 0) {
        throw new Error("Backend no devolvió archivos en /archivos.");
      }

      for (const f of files) {
        // solo UI: mostrar último archivo
        setSnapshot((prev) => ({ ...prev, last_file: f.name || "" }));

        if (supportsDirectoryPicker()) {
          await downloadOneFileViaProxy(f);
        } else {
          await fallbackDownloadInBrowser(f);
        }

        await fetch(api("/cliente/descargado"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: f.name || "" }),
        });
      }

      await fetch(api("/cliente/finalizar"), { method: "POST" });

      setUiState("finalizado");
      stopPolling();
    } catch (e) {
      setUiState("error");
      setMsg(`Error en descarga del cliente: ${String(e)}`);
      stopPolling();
    } finally {
      clientRunRef.current.running = false;
    }
  }

  /**
   * ============================================================
   *  CANCEL
   * ============================================================
   */
  async function handleCancel() {
    setMsg("");
    try {
      await fetch(api("/cancelar"), { method: "POST" });
    } catch {
      // ignore
    }
  }

  /**
   * ============================================================
   *  ON MOUNT: reanudar si backend estaba corriendo
   * ============================================================
   */
  useEffect(() => {
    let aborted = false;

    (async () => {
      try {
        const res = await fetch(api("/progreso"));
        const data = await res.json();
        if (aborted) return;

        if (data.status === "descubriendo" || data.status === "esperando_descarga_cliente") {
          applySnapshot(data);
          setUiState(data.status === "descubriendo" ? "descubriendo" : "descargando_cliente");
          startPolling();
        } else {
          await handleReset();
        }
      } catch {
        await handleReset();
      }
    })();

    return () => {
      aborted = true;
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * ============================================================
   *  Auto-start client downloads when ready
   * ============================================================
   */
  useEffect(() => {
    runClientDownloadsIfReady();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot.status, snapshot.files_count]);

  /**
   * ============================================================
   *  UI FLAGS
   * ============================================================
   */
  const showCompleted =
    uiState === "finalizado" || snapshot.status === "finalizado" || snapshot.percent >= 100;

  const busy =
    uiState === "iniciando" ||
    uiState === "descubriendo" ||
    uiState === "descargando_cliente";

  /**
   * ============================================================
   *  RENDER
   * ============================================================
   */
  return (
    <div className="container">
      <h1>Módulo de descarga de documentos en el SIGED.</h1>

      <form onSubmit={handleStart}>
        <label>🔗 URL del SIGED:</label>
        <br />

        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://cgrweb.cgr.go.cr/apex/f?p=CORRESPONDENCIA:1:...P1_CONSECUTIVO:XXXXXXXX"
          required
          disabled={busy}
        />
        <br />

        <p style={{ fontStyle: "italic", color: "gray" }}>
          📁 Se te pedirá elegir una carpeta. Dentro se creará{" "}
          <strong>{SIGED_SUBFOLDER}</strong> y ahí se guardarán los archivos.
        </p>

        <button type="submit" disabled={busy}>
          Iniciar Descarga
        </button>

        {" "}
        <button type="button" onClick={handleCancel} disabled={!busy}>
          Cancelar
        </button>

        {" "}
        {showCompleted && (
          <button type="button" onClick={handleReset}>
            Limpiar
          </button>
        )}
      </form>

      <div className="progress-section">
        <p>
          <strong>Estado UI:</strong> {uiState}
        </p>

        <p>
          <strong>Estado backend:</strong> {snapshot.status}{" "}
          <span style={{ opacity: 0.7 }}>(phase={snapshot.phase})</span>
        </p>

        {pickedFolderLabel ? (
          <p style={{ marginTop: 6 }}>
            📂 Carpeta elegida: <strong>{pickedFolderLabel}</strong> →{" "}
            <strong>{SIGED_SUBFOLDER}</strong>
          </p>
        ) : null}

        {msg && <p style={{ color: "#ffb3b3" }}>{msg}</p>}

        {snapshot.last_error && uiState !== "error" ? (
          <p style={{ color: "#ffb3b3" }}>⚠️ {snapshot.last_error}</p>
        ) : null}

        <p>
          <strong>Total (backend):</strong> {snapshot.total}
        </p>
        <p>
          <strong>Descubiertos:</strong> {snapshot.discovered_done}/{snapshot.total}
        </p>
        <p>
          <strong>Descargados en cliente:</strong> {snapshot.client_done}/{snapshot.total}
        </p>
        <p>
          <strong>Progreso total:</strong> {snapshot.percent}%
        </p>

        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${Math.min(100, snapshot.percent)}%` }} />
        </div>

        {snapshot.last_file ? (
          <p style={{ marginTop: 8 }}>
            📄 Último archivo: <em>{snapshot.last_file}</em>
          </p>
        ) : null}

        {snapshot.status === "esperando_descarga_cliente" ? (
          <p style={{ marginTop: 8, opacity: 0.9 }}>
            ✅ Archivos descubiertos. Iniciando descargas 1-a-1…
          </p>
        ) : null}

        {showCompleted && (
          <p style={{ marginTop: 8 }}>
            ✅ Descarga finalizada. Archivos en <strong>{SIGED_SUBFOLDER}</strong>.
          </p>
        )}

        {!supportsDirectoryPicker() ? (
          <p style={{ marginTop: 10, color: "gold" }}>
            Nota: tu navegador no permite escoger carpeta. Se descargarán archivos al folder de Descargas por defecto.
            Para guardar en <strong>{SIGED_SUBFOLDER}</strong> automáticamente, usa Chrome/Edge.
          </p>
        ) : null}
      </div>
    </div>
  );
}