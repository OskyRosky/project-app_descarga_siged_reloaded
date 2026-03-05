// App.jsx


import { useEffect, useRef, useState } from "react";
import "./App.css";

/* ======================================================
   🔧 CONFIGURACIÓN DEL API BACKEND
====================================================== */
const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : path);

/* ======================================================
   ✅ VALIDACIÓN DE URL DEL SIGED
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

function withAdvice(msg) {
  return `${msg} Por favor verifique de nuevo el enlace para poder descargar los archivos y vuelva a intentarlo.`;
}

/* ======================================================
   🧼 Sanitizar nombre archivo (cliente)
====================================================== */
function sanitizeFilename(name) {
  const base = (name || "archivo").trim();
  return base.replace(/[<>:"/\\|?*\x00-\x1F]/g, "_");
}

/* ======================================================
   🧠 FS Access helpers (Chrome/Edge)
====================================================== */
function hasFileSystemAccessAPI() {
  return typeof window !== "undefined" && "showDirectoryPicker" in window;
}

async function ensureSubdirSIGED(rootDirHandle) {
  // Crea/usa subcarpeta SIGED_DOCUMENTOS
  return await rootDirHandle.getDirectoryHandle("SIGED_DOCUMENTOS", { create: true });
}

async function writeBlobToFile(dirHandle, filename, blob) {
  const safe = sanitizeFilename(filename);
  const fileHandle = await dirHandle.getFileHandle(safe, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(blob);
  await writable.close();
}

/* ======================================================
   🎯 COMPONENTE PRINCIPAL
====================================================== */
export default function App() {
  const [url, setUrl] = useState("");

  // UI status (mapeado al backend)
  // inicio | iniciando… | descubriendo | esperando_descarga_cliente | descargando_cliente | finalizado | error | cancelado
  const [status, setStatus] = useState("inicio");

  // Progreso (backend combinado)
  const [total, setTotal] = useState(0);
  const [discoveredDone, setDiscoveredDone] = useState(0);
  const [clientDone, setClientDone] = useState(0);
  const [percent, setPercent] = useState(0);
  const [lastFile, setLastFile] = useState("");
  const [msg, setMsg] = useState("");

  // Manifest
  const [files, setFiles] = useState([]); // [{name,url}]
  const [downloadingClient, setDownloadingClient] = useState(false);

  // Carpeta (File System Access API)
  const [fsSupported] = useState(hasFileSystemAccessAPI());
  const [rootDirHandle, setRootDirHandle] = useState(null);
  const [sigedDirHandle, setSigedDirHandle] = useState(null);

  // Polling / control
  const pollingRef = useRef(null);
  const clientAbortRef = useRef(null);
  const clientLoopStartedRef = useRef(false);

  /* ======================================================
     ⏱️ POLLING
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

  function abortClientDownloads() {
    if (clientAbortRef.current) {
      try {
        clientAbortRef.current.abort();
      } catch {}
      clientAbortRef.current = null;
    }
    clientLoopStartedRef.current = false;
    setDownloadingClient(false);
  }

  /* ======================================================
     📦 API calls
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

      const st = data.status;

      if (st === "error") {
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

      if (st === "cancelado") {
        setStatus("cancelado");
        setMsg("");
        stopPolling();
        abortClientDownloads();
        return;
      }

      if (st === "finalizado" || (data.percent ?? 0) >= 100) {
        setStatus("finalizado");
        setMsg("");
        stopPolling();
        abortClientDownloads();
        return;
      }

      if (st === "descubriendo") {
        setStatus("descubriendo");
        return;
      }

      if (st === "esperando_descarga_cliente") {
        setStatus("esperando_descarga_cliente");
        // Arrancar loop cliente UNA sola vez
        if (!clientLoopStartedRef.current) {
          setStatus("descargando_cliente");
          await ensureManifestLoaded();
          startClientDownloadLoop();
        } else {
          setStatus("descargando_cliente");
        }
        return;
      }

      // inicio u otros
    } catch {
      setStatus("error");
      setMsg("⚠️ Error consultando progreso");
      stopPolling();
      abortClientDownloads();
    }
  }

  async function ensureManifestLoaded() {
    try {
      const res = await fetch(api("/archivos"));
      const data = await res.json();
      if (res.ok && data.ok && Array.isArray(data.files)) {
        setFiles(data.files);
        return data.files;
      }
      setFiles([]);
      return [];
    } catch {
      setFiles([]);
      return [];
    }
  }

  async function reportClientDownloaded(filename) {
    try {
      await fetch(api("/cliente/descargado"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: filename || "" }),
      });
    } catch {}
  }

  async function reportClientFinalizar() {
    try {
      await fetch(api("/cliente/finalizar"), { method: "POST" });
    } catch {}
  }

  /* ======================================================
     📁 Elegir carpeta y crear SIGED_DOCUMENTOS
  ====================================================== */
  async function handleChooseFolder() {
    if (!fsSupported) {
      setStatus("error");
      setMsg("Este navegador no soporta selección de carpeta (File System Access API). Use Chrome/Edge.");
      return;
    }
    try {
      const picked = await window.showDirectoryPicker({ mode: "readwrite" });
      const siged = await ensureSubdirSIGED(picked);

      setRootDirHandle(picked);
      setSigedDirHandle(siged);
      setMsg("✅ Carpeta lista: se usará SIGED_DOCUMENTOS dentro de la carpeta que elegiste.");
    } catch (e) {
      setStatus("error");
      setMsg(`No se pudo seleccionar carpeta: ${String(e)}`);
    }
  }

  /* ======================================================
     ⬇️ Descarga (cliente) 1 a 1
     Estrategia:
       - Preferido: /proxy (mismo origen) -> evita CORS y no guarda en servidor
       - Fallback: <a href> (descarga normal; pero SIN carpeta controlada)
  ====================================================== */
  async function downloadOneToFolder(file, signal) {
    if (!sigedDirHandle) {
      throw new Error("No hay carpeta SIGED_DOCUMENTOS seleccionada.");
    }

    const name = sanitizeFilename(file?.name || "archivo");
    const target = api(`/proxy?url=${encodeURIComponent(file.url)}&name=${encodeURIComponent(name)}`);

    const resp = await fetch(target, { signal });
    if (!resp.ok) throw new Error(`Proxy HTTP ${resp.status}`);

    const blob = await resp.blob();
    await writeBlobToFile(sigedDirHandle, name, blob);
    return name;
  }

  function downloadViaAnchor(file) {
    const name = sanitizeFilename(file?.name || "archivo");
    const a = document.createElement("a");
    a.href = file.url;
    a.download = name; // puede ser ignorado cross-origin, pero no daña
    a.rel = "noopener";
    a.target = "_blank";
    document.body.appendChild(a);
    a.click();
    a.remove();
    return name;
  }

  async function startClientDownloadLoop() {
    clientLoopStartedRef.current = true;
    setDownloadingClient(true);
    setMsg("");

    clientAbortRef.current = new AbortController();
    const { signal } = clientAbortRef.current;

    try {
      const manifest = files.length ? files : await ensureManifestLoaded();
      if (!manifest.length) {
        setStatus("error");
        setMsg("No se recibió manifest de archivos (/archivos).");
        setDownloadingClient(false);
        return;
      }

      // Si no hay FS API o no eligió carpeta, avisamos y hacemos fallback
      const canWriteFolder = !!sigedDirHandle;

      if (!canWriteFolder) {
        // Este mensaje es clave para que no haya “misterios”
        setMsg(
          "⚠️ No hay carpeta seleccionada (SIGED_DOCUMENTOS). " +
            "Haré descargas normales del navegador (sin poder meterlas en carpeta automáticamente). " +
            "Si querés carpeta real, presioná “Elegir carpeta” (Chrome/Edge) y repetí."
        );
      }

      // Descarga secuencial
      for (let i = 0; i < manifest.length; i++) {
        if (signal.aborted) break;
        const f = manifest[i];

        // 2 reintentos por archivo
        let ok = false;
        let lastErr = null;

        for (let attempt = 1; attempt <= 2; attempt++) {
          try {
            const savedName = canWriteFolder
              ? await downloadOneToFolder(f, signal)
              : downloadViaAnchor(f);

            setLastFile(savedName);
            await reportClientDownloaded(savedName);
            ok = true;
            break;
          } catch (e) {
            lastErr = e;
            // micro pausa
            await new Promise((r) => setTimeout(r, 350));
          }
        }

        if (!ok) {
          throw new Error(`Error en descarga cliente: ${String(lastErr || "desconocido")}`);
        }
      }

      await reportClientFinalizar();
      setDownloadingClient(false);
    } catch (e) {
      setDownloadingClient(false);
      setStatus("error");
      setMsg(withAdvice(`Error en descarga cliente: ${String(e?.message || e)}`));
    }
  }

  /* ======================================================
     🚀 INICIAR (submit)
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

    // Reset UI
    stopPolling();
    abortClientDownloads();

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
        setStatus("descubriendo");
        startPolling();
      } else {
        const base = data.detail || data.message || "No se pudo iniciar.";
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
     🔁 LIMPIAR
  ====================================================== */
  function handleReset() {
    stopPolling();
    abortClientDownloads();
    setStatus("inicio");
    setTotal(0);
    setDiscoveredDone(0);
    setClientDone(0);
    setPercent(0);
    setLastFile("");
    setUrl("");
    setMsg("");
    setFiles([]);
  }

  /* ======================================================
     🧩 AL MONTAR
  ====================================================== */
  useEffect(() => {
    let aborted = false;

    (async () => {
      try {
        const res = await fetch(api("/progreso"));
        const data = await res.json();
        if (aborted) return;

        // Si el backend está en medio de discovery o esperando cliente, retomamos polling
        if (data.status === "descubriendo" || data.status === "esperando_descarga_cliente") {
          startPolling();
        } else {
          handleReset();
        }
      } catch {
        handleReset();
      }
    })();

    return () => {
      aborted = true;
      stopPolling();
      abortClientDownloads();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const showCompleted = percent >= 100 || status === "finalizado";
  const inputDisabled =
    status === "descubriendo" ||
    status === "esperando_descarga_cliente" ||
    status === "descargando_cliente" ||
    status === "iniciando…" ||
    downloadingClient;

  /* ======================================================
     💅 RENDER
  ====================================================== */
  return (
    <div className="container">
      <h1>Módulo de descarga de documentos en el SIGED.</h1>

      <div style={{ marginBottom: 12 }}>
        <button type="button" onClick={handleChooseFolder} disabled={!fsSupported || inputDisabled}>
          📁 Elegir carpeta (para crear/usar SIGED_DOCUMENTOS)
        </button>
        {!fsSupported ? (
          <p style={{ marginTop: 6, color: "gray", fontStyle: "italic" }}>
            Tu navegador no soporta selección de carpeta. Usá Chrome/Edge para guardar dentro de SIGED_DOCUMENTOS.
          </p>
        ) : sigedDirHandle ? (
          <p style={{ marginTop: 6, color: "gray", fontStyle: "italic" }}>
            ✅ SIGED_DOCUMENTOS listo (se guardará dentro de la carpeta que elegiste).
          </p>
        ) : (
          <p style={{ marginTop: 6, color: "gray", fontStyle: "italic" }}>
            Recomendado: elegí una carpeta (por ejemplo “Downloads”) y la app creará/usar&aacute; SIGED_DOCUMENTOS.
          </p>
        )}
      </div>

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

        <button type="submit" disabled={inputDisabled}>
          Iniciar Proceso
        </button>

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
        <p>
          <strong>Estado:</strong> {status}
        </p>
        {msg && <p style={{ color: "#ffb3b3" }}>{msg}</p>}

        <p>
          <strong>Total de documentos:</strong> {total}
        </p>
        <p>
          <strong>Discovery:</strong> {discoveredDone}/{total} — <strong>Cliente:</strong> {clientDone}/{total}
        </p>
        <p>
          <strong>Progreso:</strong> {percent}%
        </p>

        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${Math.min(100, percent)}%` }} />
        </div>

        {lastFile ? (
          <p style={{ marginTop: 8 }}>
            📄 Último archivo: <em>{lastFile}</em>
          </p>
        ) : null}

        {showCompleted && (
          <p style={{ marginTop: 8 }}>
            ✅ Proceso finalizado. (Archivos guardados en <strong>SIGED_DOCUMENTOS</strong> si elegiste carpeta).
          </p>
        )}
      </div>
    </div>
  );
}