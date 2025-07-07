// G:\Learning\F1Data\F1Data_Web\src\Quadrant1Sessions.js
import React, { useState, useEffect } from 'react';

function Quadrant1Sessions({ meetingKey }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:30080';

  useEffect(() => {
    if (meetingKey) {
      const fetchSessions = async () => {
        setLoading(true);
        setError(null);
        try {
          const apiUrl = `${API_BASE_URL}/api/sessions-by-meeting/?meeting_key=${meetingKey}`;
          console.log('DEBUG Q1: URL da API de sessões:', apiUrl);

          const response = await fetch(apiUrl);

          if (!response.ok) {
            throw new Error(`Erro HTTP: ${response.status} ${response.statusText}`);
          }

          const data = await response.json();
          console.log('DEBUG Q1: Dados de sessões recebidos:', data);

          setSessions(data); 
        } catch (err) {
          console.error('Erro ao buscar sessões:', err);
          setError(err.message || 'Erro ao carregar sessões.');
        } finally {
          setLoading(false);
        }
      };
      fetchSessions();
    } else {
      setSessions([]); 
      setLoading(false);
    }
  }, [meetingKey, API_BASE_URL]); 

  // Função auxiliar para formatar a data como "YYYY-MM-DD HH:MM:SS"
  const formatSessionDate = (dateString) => {
    if (!dateString) return 'N/A';
    const parts = dateString.split('T');
    if (parts.length > 1) {
        const timePart = parts[1].split(/[+-Z]/)[0];
        return `${parts[0]} ${timePart}`;
    }
    return dateString.replace('Z', '').replace('T', ' '); 
  };

  const handleSessionClick = (sessionKey) => {
      console.log(`Sessão clicada: ${sessionKey}`);
  };

  if (loading) {
    return <p>Carregando sessões...</p>;
  }

  if (error) {
    return <p style={{ color: 'red' }}>Erro: {error}</p>;
  }

  if (sessions.length === 0) {
    return <p>Nenhuma sessão encontrada para este meeting.</p>;
  }
  return (
    <div className="sessions-container-box no-bg">
      <div className="session-table-header">
        <span>Evento</span>
        <span>Data</span>
      </div>

      {sessions.map(session => (
        <a
          key={session.session_key}
          href="#"
          onClick={(e) => {
            e.preventDefault();
            handleSessionClick(session.session_key);
          }}
          className="session-table-link-row"
        >
          <span>{session.session_name || 'N/A'}</span>
          <span>{formatSessionDate(session.date_start)}</span>
        </a>
      ))}
    </div>
  );
}

export default Quadrant1Sessions;