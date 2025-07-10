// G:\Learning\F1Data\F1Data_Web\src\Quadrant1Sessions.js
import React, { useState, useEffect } from 'react';

function Quadrant1Sessions({ meetingKey, onSessionSelect, selectedSessionKey }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:30080';

  useEffect(() => {
    const fetchSessions = async () => {
      if (!meetingKey) {
        setSessions([]);
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const response = await fetch(`${API_BASE_URL}/api/sessions-by-meeting/?meeting_key=${meetingKey}`);
        if (!response.ok) {
          throw new Error(`Erro ao buscar sessões: ${response.status} ${response.statusText}`);
        }
        const data = await response.json();
        setSessions(data);
        console.log('DEBUG Quadrant1Sessions: Sessões carregadas para o meetingKey:', meetingKey, data);
      } catch (err) {
        console.error('Erro em Quadrant1Sessions:', err);
        setError(err.message || 'Erro ao carregar sessões.');
      } finally {
        setLoading(false);
      }
    };

    fetchSessions();
  }, [meetingKey, API_BASE_URL]);

  const formatSessionDate = (dateString) => {
    if (!dateString) return 'N/A';
    const parts = dateString.split('T');
    if (parts.length > 1) {
        const timePart = parts[1].split(/[+-Z]/)[0];
        return `${parts[0]} ${timePart}`;
    }
    return dateString.replace('Z', '').replace('T', ' ');
  };

  // MODIFICADO AQUI: Agora passa session.date_end como quarto argumento
  const handleSessionClick = (session) => {
      onSessionSelect(session.session_key, session.session_name, session.date_start, session.date_end);
  };

  if (loading) {
    return <p>Carregando sessões...</p>;
  }

  if (error) {
    return <p style={{ color: 'red' }}>Erro: {error}</p>;
  }

  if (sessions.length === 0) {
    return <p>Nenhuma sessão encontrada para este evento.</p>;
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
            handleSessionClick(session);
          }}
          className={`session-table-link-row ${selectedSessionKey === session.session_key ? 'active' : ''}`}
        >
          <span>{session.session_name || 'N/A'}</span>
          <span>{formatSessionDate(session.date_start)}</span>
        </a>
      ))}
    </div>
  );
}

export default Quadrant1Sessions;