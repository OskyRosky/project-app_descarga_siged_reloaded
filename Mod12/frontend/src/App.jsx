import { useEffect, useRef, useState } from "react";
import "./App.css";

/**
 * ============================================================
 *  CONFIG
 * ============================================================
 */
const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : path);

// Subcarpeta fija que SIEMPRE se crea dentro de la carpeta elegida
const SIGED_SUBFOLDER = "SIGED_DOCUMENTOS";

// Polling (ms)
const POLL_MS = 800;

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
   * uiState:
   *  - inicio
   *  - iniciando
   *  - descubriendo
   *  - descargando_cliente
   *  - confirmando_backend   (descarga cliente terminó, esperando backend finalizado)
   *  - finalizado
   *  - error
   *  - cancelado
   */
  const [uiState, setUiState] = useState("inicio");
  const [msg, setMsg] = useState("");

  /**
   * ------------------------------------------------------------
   * BACKEND SNAPSHOT (UN SOLO STATE)
   * ------------------------------------------------------------
   * status:
   *  - inicio
   *  - descubriendo
   *  - esperando_descarga_cliente
   *  - error
   *  - cancelado
   *  - finalizado
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
    pollingRef.current = setInterval(fetchProgreso, POLL_MS);
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

      applySnapshot(data);

      // Manejo de estados terminales del backend
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

      // Estados no terminales
      if (data.status === "descubriendo") {
        setUiState((prev) => (prev === "confirmando_backend" ? prev : "descubriendo"));
        return;
      }

      if (data.status === "esperando_descarga_cliente") {
        // seguimos polling mientras el cliente descarga o mientras confirmamos backend
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
   * Pide seleccionar una carpeta y luego crea SIGED_DOCUMENTOS dentro.
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

      // Siempre creamos/obtenemos SIGED_DOCUMENTOS dentro
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
      setMsg(
        withAdvice("URL inválida. Debe ser de cgrweb.cgr.go.cr con CORRESPONDENCIA:1 y P1_CONSECUTIVO.")
      );
      return;
    }

    // Pedimos carpeta antes de iniciar backend (opción 2)
    const okFolder = await pickFolder();
    if (!okFolder) return;

    setUiState("iniciando");

    // Reset visual (front)
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
   *  DESCARGA 1 ARCHIVO (via /proxy) -> SIGED_DOCUMENTOS
   * ============================================================
   * Importante: escribimos por chunks y cerramos explícitamente el archivo
   * antes de reportar /cliente/descargado (reduce desfases y “partial writes”).
   */
  async function downloadOneFileViaProxy(fileObj) {
    const sigedDir = sigedDirHandleRef.current;
    if (!sigedDir) throw new Error("No hay carpeta SIGED_DOCUMENTOS seleccionada.");

    const fileName = fileObj.name || "archivo";
    const targetUrl = fileObj.url;

    const proxyUrl =
      api("/proxy") + `?url=${encodeURIComponent(targetUrl)}&name=${encodeURIComponent(fileName)}`;

    const resp = await fetch(proxyUrl);
    if (!resp.ok) {
      const t = await resp.text().catch(() => "");
      throw new Error(`Proxy HTTP ${resp.status}: ${t}`);
    }

    const fileHandle = await sigedDir.getFileHandle(fileName, { create: true });
    const writable = await fileHandle.createWritable();

    try {
      if (!resp.body) {
        // fallback blob
        const blob = await resp.blob();
        await writable.write(blob);
        await writable.close();
        return;
      }

      // stream manual -> close garantizado
      const reader = resp.body.getReader();
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value) await writable.write(value);
      }
      await writable.close();
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
   * (Sin control de carpeta)
   */
  async function fallbackDownloadInBrowser(fileObj) {
    const fileName = fileObj.name || "archivo";
    const targetUrl = fileObj.url;

    const proxyUrl =
      api("/proxy") + `?url=${encodeURIComponent(targetUrl)}&name=${encodeURIComponent(fileName)}`;

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

      // Descarga secuencial 1-a-1
      for (const f of files) {
        setSnapshot((prev) => ({ ...prev, last_file: f.name || "" }));

        if (supportsDirectoryPicker()) {
          await downloadOneFileViaProxy(f);
        } else {
          await fallbackDownloadInBrowser(f);
        }

        // Reportar al backend solo cuando el archivo ya fue escrito/cerrado
        await fetch(api("/cliente/descargado"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: f.name || "" }),
        });
      }

      // Pedimos al backend marcar finalizado
      await fetch(api("/cliente/finalizar"), { method: "POST" });

      // CLAVE: NO ponemos "finalizado" aquí todavía.
      // Pasamos a "confirmando_backend" y mantenemos polling hasta que /progreso.status === "finalizado".
      setUiState("confirmando_backend");

      // Asegura que el polling siga vivo (por si alguien lo detuvo)
      startPolling();
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
    uiState === "descargando_cliente" ||
    uiState === "confirmando_backend";

  /**
   * ============================================================
   *  RENDER
   * ============================================================
   */
  return (
    <div className="container">
      <h1>Módulo de descarga de documentos en el SIGED.</h1>

      <div style={{ marginBottom: 14, padding: 12, borderRadius: 8, background: "rgba(255,255,255,0.04)" }}>
        <p style={{ marginTop: 0, marginBottom: 8 }}>
          <strong>Antes de iniciar:</strong>
        </p>
        <ul style={{ marginTop: 0 }}>
          <li>✅ Solo se permiten enlaces del tipo <strong>cgrweb.cgr.go.cr</strong>.</li>
          <li>📁 Debe seleccionar una carpeta destino. Dentro se creará <strong>{SIGED_SUBFOLDER}</strong>.</li>
          <li>
            ⚠️ No seleccione carpetas “raíz” o sensibles (por ejemplo: Descargas, raíz del disco, etc.). Si el navegador detecta <em>system files</em>,
            aparecerá una advertencia. Cree una carpeta nueva y seleccione esa.
          </li>
          <li>
            🔒 La primera vez el navegador pedirá permiso de escritura en la carpeta seleccionada. Para continuar, debe presionar <strong>Permitir</strong> o <strong>Allow</strong>.
          </li>
          <li>
            ⏱️ Puede existir un desfase visual de <strong> 15 a 30 segundos </strong> entre el progreso mostrado por  la AppSIGED y la carpeta de descarga de los documetos.
            Aun así, el sistema descargará todos los documentos contenidos en el enlace.
          </li>
        </ul>
      </div>

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

        <p style={{ fontStyle: "italic", color: "gray", marginTop: 10 }}>
          📁 Se te pedirá elegir una carpeta. Dentro se creará <strong>{SIGED_SUBFOLDER}</strong> y ahí se guardarán los archivos.
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

        {uiState === "confirmando_backend" ? (
          <p style={{ marginTop: 8, opacity: 0.9 }}>
            ⏳ Descarga cliente completada. Confirmando finalización con el backend…
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