// Importamos los hooks de React y el CSS principal
import { useEffect, useRef, useState } from "react";
import "./App.css";

/* ======================================================
   🔧 CONFIGURACIÓN DEL API BACKEND
   ------------------------------------------------------
   - Si definimos VITE_API_BASE, la usamos (p.ej. http://127.0.0.1:8210)
   - Si no existe, usamos el mismo origen donde se sirva el frontend (Vite proxy)
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
   🧼 Sanitizar nombre de archivo (cliente)
====================================================== */
function sanitizeFilename(name) {
  const base = (name || "archivo").trim();
  // Evita caracteres problemáticos en Windows/macOS
  return base.replace(/[<>:"/\\|?*\x00-\x1F]/g, "_");
}

/* ======================================================
   📁 “Carpeta” lógica: prefijo en filename
   Nota: navegador NO puede crear carpetas automáticamente.
====================================================== */
const DOWNLOAD_PREFIX = "SIGED_DOCUMENTOS__";

/* ======================================================
   🎯 COMPONENTE PRINCIPAL
====================================================== */
export default function App() {
  // --- ESTADOS PRINCIPALES ---
  const [url, setUrl] = useState("");

  // Estados UI (alineados a backend nuevo)
  // inicio | iniciando… | descubriendo | descargando_cliente | finalizado | error | cancelado
  const [status, setStatus] = useState("inicio");

  // Progreso backend (discovery + client)
  const [total, setTotal] = useState(0);
  const [discoveredDone, setDiscoveredDone] = useState(0);
  const [clientDone, setClientDone] = useState(0);
  const [percent, setPercent] = useState(0);
  const [lastFile, setLastFile] = useState("");
  const [msg, setMsg] = useState("");

  // Manifest
  const [files, setFiles] = useState([]); // [{name,url}]
  const [downloading, setDownloading] = useState(false);

  // Control del polling
  const pollingRef = useRef(null);

  // Control de descarga cliente (cancelable)
  const clientAbortRef = useRef(null);

  // Evita arrancar la descarga cliente dos veces
  const clientLoopStartedRef = useRef(false);

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
     🧯 Cancelar loop de descargas cliente (local)
  ====================================================== */
  function abortClientDownloads() {
    if (clientAbortRef.current) {
      try {
        clientAbortRef.current.abort();
      } catch {}
      clientAbortRef.current = null;
    }
    clientLoopStartedRef.current = false;
    setDownloading(false);
  }

  /* ======================================================
     📡 CONSULTAR PROGRESO
  ====================================================== */
  async function fetchProgreso() {
    try {
      const res = await fetch(api("/progreso"));
      const data = await res.json();

      setTotal(data.total ?? 0);
      setDiscoveredDone(data.discovered_done ?? 0);
      setClientDone(data.client_done ?? 0);
      setPercent(data.percent ?? 0);
      setLastFile(data.last_file || "");

      // ---- estados backend: inicio | descubriendo | esperando_descarga_cliente | finalizado | error | cancelado
      if (data.status === "error") {
        setStatus("error");
        const base = data.last_error || "Error en el proceso.";
        const needsAdvice =
          /no se encontraron enlaces/i.test(base) ||
          /url inválida|dominio no permitido/i.test(base);
        setMsg(needsAdvice ? withAdvice(base) : base);
        stopPolling();
        abortClientDownloads();
        return;
      }

      if (data.status === "cancelado") {
        setStatus("cancelado");
        setMsg("");
        stopPolling();
        abortClientDownloads();
        return;
      }

      if (data.status === "finalizado" || (data.percent ?? 0) >= 100) {
        setStatus("finalizado");
        setMsg("");
        stopPolling();
        abortClientDownloads();
        return;
      }

      if (data.status === "descubriendo") {
        setStatus("descubriendo");
        return;
      }

      if (data.status === "esperando_descarga_cliente") {
        // Backend listo -> si todavía no arrancamos el cliente, arrancamos
        if (!clientLoopStartedRef.current) {
          setStatus("descargando_cliente");
          await ensureManifestLoaded();
          // Arrancar loop cliente (no bloquea UI)
          startClientDownloadLoop();
        } else {
          setStatus("descargando_cliente");
        }
        return;
      }

      // data.status === "inicio" -> no hacemos nada agresivo
    } catch {
      setStatus("error");
      setMsg("⚠️ Error consultando progreso");
      stopPolling();
      abortClientDownloads();
    }
  }

  /* ======================================================
     📦 Cargar manifest (/archivos)
  ====================================================== */
  async function ensureManifestLoaded() {
    try {
      const res = await fetch(api("/archivos"));
      const data = await res.json();
      if (res.ok && data.ok) {
        const list = Array.isArray(data.files) ? data.files : [];
        setFiles(list);
        return list;
      }
      return [];
    } catch {
      return [];
    }
  }

  /* ======================================================
     ⬇️ Descargar 1 archivo en cliente (cualquier extensión)
  ====================================================== */
  async function downloadOneFile(file, signal) {
    // file: {name,url}
    const rawName = sanitizeFilename(file?.name || "archivo");
    const filename = `${DOWNLOAD_PREFIX}${rawName}`;

    const resp = await fetch(file.url, { signal });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status} descargando ${rawName}`);
    }

    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);

    // Descarga invisible
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();

    // Limpieza
    setTimeout(() => {
      try {
        URL.revokeObjectURL(blobUrl);
      } catch {}
      try {
        document.body.removeChild(a);
      } catch {}
    }, 1500);

    return filename;
  }

  /* ======================================================
     🔁 Loop cliente: descarga secuencial + reporta al backend
====================================================== */
  async function startClientDownloadLoop() {
    clientLoopStartedRef.current = true;
    setDownloading(true);

    const controller = new AbortController();
    clientAbortRef.current = controller;

    try {
      // Asegurar manifest
      const manifest = files.length ? files : (await ensureManifestLoaded());

      if (!manifest || manifest.length === 0) {
        throw new Error("No se pudo obtener la lista de archivos (/archivos).");
      }

      // Si el backend ya tiene client_done > 0 (por reconexión), saltamos esos
      // Asumimos orden estable (como viene en /archivos).
      const startIdx = Math.min(clientDone ?? 0, manifest.length);

      for (let i = startIdx; i < manifest.length; i++) {
        if (controller.signal.aborted) {
          throw new Error("Cancelado por el usuario.");
        }

        const f = manifest[i];

        // 1) Descargar en cliente
        const savedName = await downloadOneFile(f, controller.signal);

        // 2) Reportar al backend (sube client_done + actualiza last_file + percent)
        await fetch(api("/cliente/descargado"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: savedName }),
          signal: controller.signal,
        });

        // pequeño respiro para no saturar
        await new Promise((r) => setTimeout(r, 80));
      }

      // 3) Finalizar en backend (forzar 100% y estado finalizado)
      await fetch(api("/cliente/finalizar"), {
        method: "POST",
        signal: controller.signal,
      });

      // Refrescar progreso una vez
      await fetchProgreso();
    } catch (e) {
      // Si fue abort explícito, lo consideramos cancelado
      const msgErr = String(e?.message || e);
      if (/cancelado/i.test(msgErr) || /aborted/i.test(msgErr)) {
        setStatus("cancelado");
        setMsg("");
      } else {
        setStatus("error");
        setMsg(withAdvice(`Error en descarga cliente: ${msgErr}`));
      }
    } finally {
      setDownloading(false);
    }
  }

  /* ======================================================
     🚀 INICIAR (submit del formulario)
  ====================================================== */
  async function handleDownload(e) {
    e.preventDefault();
    setMsg("");

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
    abortClientDownloads();
    stopPolling();

    setStatus("iniciando…");
    setTotal(0);
    setDiscoveredDone(0);
    setClientDone(0);
    setPercent(0);
    setLastFile("");
    setFiles([]);

    try {
      const res = await fetch(api("/descargar"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const data = await res.json();

      if (res.ok && data.ok) {
        // Arrancar polling (el backend pasará por discovery y luego waiting_client)
        setStatus("descubriendo");
        startPolling();
      } else {
        const base = data.detail || data.message || "No se pudo iniciar.";
        const needsAdvice =
          res.status === 400 ||
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
     🧹 LIMPIAR (Frontend + Backend)
     - Limpia UI
     - Resetea backend para arrancar “limpio”
  ====================================================== */
  async function handleReset() {
    abortClientDownloads();
    stopPolling();

    try {
      await fetch(api("/reset"), { method: "POST" });
    } catch {}

    setStatus("inicio");
    setTotal(0);
    setDiscoveredDone(0);
    setClientDone(0);
    setPercent(0);
    setLastFile("");
    setFiles([]);
    setUrl("");
    setMsg("");
  }

  /* ======================================================
     ✋ CANCELAR TODO
     - Cancela backend (si está corriendo discovery)
     - Aborta descargas cliente
  ====================================================== */
  async function handleCancel() {
    try {
      await fetch(api("/cancelar"), { method: "POST" });
    } catch {}
    abortClientDownloads();
    stopPolling();
    setStatus("cancelado");
  }

  /* ======================================================
     🧩 AL MONTAR LA PÁGINA
     - Si hay un proceso vivo: retomamos polling.
     - Si estaba esperando cliente: retomamos descarga cliente.
     - Si no: pantalla limpia.
  ====================================================== */
  useEffect(() => {
    let aborted = false;

    (async () => {
      try {
        const res = await fetch(api("/progreso"));
        const data = await res.json();
        if (aborted) return;

        if (data.status === "descubriendo") {
          setStatus("descubriendo");
          setTotal(data.total ?? 0);
          setDiscoveredDone(data.discovered_done ?? 0);
          setClientDone(data.client_done ?? 0);
          setPercent(data.percent ?? 0);
          setLastFile(data.last_file || "");
          startPolling();
          return;
        }

        if (data.status === "esperando_descarga_cliente") {
          setStatus("descargando_cliente");
          setTotal(data.total ?? 0);
          setDiscoveredDone(data.discovered_done ?? 0);
          setClientDone(data.client_done ?? 0);
          setPercent(data.percent ?? 0);
          setLastFile(data.last_file || "");
          startPolling();
          // Polling va a disparar startClientDownloadLoop() si no ha iniciado
          return;
        }

        // cualquier otro -> limpio
        await handleReset();
      } catch {
        await handleReset();
      }
    })();

    return () => {
      aborted = true;
      abortClientDownloads();
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const showCompleted = percent >= 100 || status === "finalizado";
  const inputDisabled =
    status === "descubriendo" ||
    status === "descargando_cliente" ||
    status === "iniciando…" ||
    downloading;

  /* ======================================================
     💅 RENDER
  ====================================================== */
  return (
    <div className="container">
      <h1>Módulo de descarga de documentos en el SIGED.</h1>

      <form onSubmit={handleDownload}>
        <label>🔗 URL del SIGED:</label>
        <br />
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://cgrweb.cgr.go.cr/apex/f?p=CORRESPONDENCIA:1:...P1_CONSECUTIVO:XXXXXXXX"
          required
          disabled={inputDisabled}
        />
        <br />

        <p style={{ fontStyle: "italic", color: "gray" }}>
          📁 En navegador no se puede crear una carpeta automáticamente.
          Los archivos se descargarán con prefijo <strong>{DOWNLOAD_PREFIX}</strong> en la carpeta de descargas.
        </p>

        <button type="submit" disabled={inputDisabled}>
          Iniciar Descarga
        </button>

        {/* Cancelar aparece durante discovery/cliente */}
        {(status === "descubriendo" || status === "descargando_cliente") && (
          <>
            {" "}
            <button type="button" onClick={handleCancel}>
              Cancelar
            </button>
          </>
        )}

        {/* Limpiar aparece al finalizar o error/cancel */}
        {(showCompleted || status === "error" || status === "cancelado") && (
          <>
            {" "}
            <button type="button" onClick={handleReset}>
              Limpiar
            </button>
          </>
        )}
      </form>

      <div className="progress-section">
        <p>
          <strong>Estado:</strong>{" "}
          {status === "descargando_cliente"
            ? "descargando (cliente)"
            : status}
        </p>

        {msg && <p style={{ color: "#ffb3b3" }}>{msg}</p>}

        <p>
          <strong>Total de documentos:</strong> {total}
        </p>

        <p>
          <strong>Discovery (backend):</strong> {discoveredDone}/{total}
        </p>

        <p>
          <strong>Descarga (cliente):</strong> {clientDone}/{total}
        </p>

        <p>
          <strong>Progreso:</strong> {percent}%
        </p>

        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{ width: `${Math.min(100, percent)}%` }}
          />
        </div>

        {lastFile ? (
          <p style={{ marginTop: 8 }}>
            📄 Último archivo: <em>{lastFile}</em>
          </p>
        ) : null}

        {showCompleted && (
          <p style={{ marginTop: 8 }}>
            ✅ {total} documentos descargados (prefijo <strong>{DOWNLOAD_PREFIX}</strong>).
          </p>
        )}
      </div>
    </div>
  );
}