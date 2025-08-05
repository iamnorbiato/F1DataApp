/* G:\Learning\F1Data\F1Data_Web\src\MeetingsList.js V22 */
import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from './api'; // ajuste o caminho se necessário
console.log('API_BASE_URL:', API_BASE_URL);

function MeetingsList({ selectedYear, onMeetingSelect, selectedMeetingKey }) {
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchMeetings = async () => {
      if (!selectedYear) { 
        setMeetings([]);
        setLoading(false);
        return;
      }

      setLoading(true); 
      setError(null);   
      try {
        console.log(`Buscando meetings para o ano ${selectedYear} da API...`);
        const response = await fetch(`${API_BASE_URL}/api/filters/meetings/?year=${selectedYear}`);
        if (!response.ok) {
          throw new Error(`Erro ao buscar meetings: ${response.status}`);
        }
        const data = await response.json();
        setMeetings(data || []); 
        console.log(`Meetings para ${selectedYear} recebidos:`, data);
      } catch (err) {
        console.error(`Erro ao carregar meetings para o ano ${selectedYear}:`, err);
        setError(err.message || 'Erro ao carregar meetings.');
      } finally {
        setLoading(false); 
      }
    };

    fetchMeetings();
  }, [selectedYear]);

  if (loading) {
    return <p>Carregando corridas de {selectedYear}...</p>;
  }

  if (error) {
    return <p style={{ color: 'red' }}>Erro: {error}</p>;
  }

  if (meetings.length === 0) {
    return <p>Nenhuma corrida encontrada para {selectedYear}.</p>;
  }

  return (
    <div className="meetings-list-container">
      <h2>Corridas de {selectedYear}</h2>
      <ul>
        {meetings.map(meeting => (
          <li
            key={meeting.meeting_key}
            // INÍCIO DA ALTERAÇÃO: Aplica a classe 'active' se o meeting_key for o selecionado
            className={`meeting-list-item ${meeting.meeting_key === selectedMeetingKey ? 'active' : ''}`} 
            // FIM DA ALTERAÇÃO
            onClick={() => onMeetingSelect(meeting.meeting_key, meeting.meeting_name, meeting.circuit_short_name, meeting.circuit_key)}
          >
            <span>{meeting.meeting_name}</span><span>{meeting.circuit_short_name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default MeetingsList;