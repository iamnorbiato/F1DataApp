// G:\Learning\F1Data\F1Data_Web\src\Sessions.js V2
import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from './api'; // ajuste o caminho se necessário
console.log('API_BASE_URL:', API_BASE_URL);

function Sessions({ meetingKey, onSessionSelect, selectedSessionKey }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

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
        console.log('DEBUG Sessions: Sessões carregadas para o meetingKey:', meetingKey, data);
      } catch (err) {
        console.error('Erro em Sessions:', err);
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
    <div className="sessions-container-box">
      {sessions.map(session => (
        <a
          key={session.session_key}
          href="#"
          onClick={(e) => {
            e.preventDefault();
            handleSessionClick(session);
          }}
          className={`session-list-item ${selectedSessionKey === session.session_key ? 'active' : ''}`}
        >
          <span>{session.session_name || 'N/A'}</span>
          <span>{formatSessionDate(session.date_start)}</span>
        </a>
      ))}
    </div>
  );
}

export default Sessions;