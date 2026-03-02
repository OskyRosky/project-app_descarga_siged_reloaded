import { useState } from 'react';
import './App.css';

function App() {
  const [url, setUrl] = useState('');
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('');

  const handleDownload = async (e) => {
    e.preventDefault();
    setStatus('');

    if (!url.startsWith('http')) {
      setStatus('❌ Ingrese una URL válida que comience con http o https');
      return;
    }

    setStatus('Iniciando descarga...');
    setProgress(0);

    try {
      const response = await fetch('http://127.0.0.1:8000/descargar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });

      const data = await response.json();
      setStatus(data.status || 'Descarga iniciada');

      // Simulación de progreso
      const interval = setInterval(() => {
        setProgress(prev => {
          if (prev >= 100) {
            clearInterval(interval);
            setStatus('Descarga finalizada (simulada)');
            return 100;
          }
          return prev + 10;
        });
      }, 300);

    } catch (error) {
      setStatus('❌ Error al iniciar la descarga');
      console.error(error);
    }
  };

  return (
    <div className="container">
      <h1>📥 Módulo de Descarga SIGED Reloaded</h1>
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
          📁 Los archivos se guardarán en la carpeta <strong>"SIGED_DOCUMENTOS"</strong> dentro de tu carpeta de descargas.
        </p><br />

        <button type="submit">Iniciar Descarga</button>
      </form>

      <div className="progress-section">
        <label>Progreso:</label>
        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${progress}%` }}></div>
        </div>
        <p>{progress}%</p>
        <p><strong>Estado:</strong> {status}</p>
      </div>
    </div>
  );
}

export default App;