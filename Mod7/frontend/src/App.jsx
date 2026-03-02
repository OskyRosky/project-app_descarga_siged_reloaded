import { useState } from 'react';
import './App.css';

function App() {
  const [url, setUrl] = useState('');
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('');

  const handleDownload = async (e) => {
    e.preventDefault();
    setStatus('');
    setProgress(0);

    if (!/^https?:\/\//i.test(url)) {
      setStatus('⚠️ Ingrese una URL válida que comience con http o https');
      return;
    }

    setStatus('Iniciando descarga...');
    let interval;

    try {
      const response = await fetch('/descargar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });

      if (!response.ok) {
        const msg = `⚠️ Error ${response.status}`;
        setStatus(msg);
        return;
      }

      let data = {};
      try { data = await response.json(); } catch { /* puede no traer JSON */ }
      setStatus(data.status || 'Descarga iniciada');

      interval = setInterval(() => {
        setProgress(prev => {
          if (prev >= 100) {
            clearInterval(interval);
            setStatus('Descarga finalizada (simulada)');
            return 100;
          }
          return prev + 10;
        });
      }, 300);

    } catch (err) {
      setStatus('⚠️ Error al iniciar la descarga');
      console.error(err);
    } finally {
      // por si salimos temprano
      setTimeout(() => clearInterval(interval), 0);
    }
  };

  return (
    <div className="container">
      <h1>🔥 Módulo de Descarga SIGED Reloaded</h1>
      <form onSubmit={handleDownload}>
        <label>🔗 URL del SIGED/ZHED:</label><br />
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          placeholder="https://..."
        /><br />

        <p style={{ fontStyle: 'italic', color: 'gray' }}>
          📁 Los archivos se guardarán en <strong>Descargas/SIGED_DOCUMENTOS</strong>.
        </p><br />

        <button type="submit">Iniciar Descarga</button>
      </form>

      <div className="progress-section">
        <label>Progreso:</label>
        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${progress}%` }} />
        </div>
        <p>{progress}%</p>
        <p><strong>Estado:</strong> {status}</p>
      </div>
    </div>
  );
}

export default App;