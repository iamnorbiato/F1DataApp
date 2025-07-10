// G:\Learning\F1Data\F1Data_Web\src\MainContent.js
import React, { useState, useEffect } from 'react';
import Quadrant1Sessions from './Quadrant1Sessions';
import DriversList from './DriversList';
import TrackMap from './TrackMap';

// Função para converter uma data para ISO string UTC com "Z" no final
function toUTCISOString(dateStr) {
  if (!dateStr) return null;
  const date = new Date(dateStr);
  if (isNaN(date)) return null; // Verifica data inválida

  // Usar método nativo toISOString() para garantir UTC com 'Z'
  return date.toISOString();
}

function MainContent({ meetingKey }) {
  const [selectedSessionKey, setSelectedSessionKey] = useState(null);
  const [selectedSessionName, setSelectedSessionName] = useState('');
  const [selectedSessionStartDate, setSelectedSessionStartDate] = useState(null);
  const [selectedSessionEndDate, setSelectedSessionEndDate] = useState(null);
  const [selectedDriverForTrackMap, setSelectedDriverForTrackMap] = useState(null);

  // Reseta os estados ao mudar de evento
  useEffect(() => {
    setSelectedSessionKey(null);
    setSelectedSessionName('');
    setSelectedSessionStartDate(null);
    setSelectedSessionEndDate(null);
    setSelectedDriverForTrackMap(null);
  }, [meetingKey]);

  const handleSessionSelect = (sessionKey, sessionName, startDate, endDate) => {
    setSelectedSessionKey(sessionKey);
    setSelectedSessionName(sessionName);
    setSelectedSessionStartDate(startDate);
    setSelectedSessionEndDate(endDate);
    setSelectedDriverForTrackMap(null);
    console.log(`MainContent: Sessão selecionada - Key: ${sessionKey}, Nome: ${sessionName}, Data Início: ${startDate}, Data Fim: ${endDate}`);
  };

  const handleDriverSelectForTrackMap = (driver) => {
    setSelectedDriverForTrackMap(driver);
    console.log(`MainContent: Driver selecionado para o mapa - ${driver.full_name || driver.driver_number}`);
  };

  if (!meetingKey) {
    return (
      <div className="welcome-message">
        <p>Selecione um evento no menu para começar.</p>
      </div>
    );
  }

  return (
    <div className="main-content-grid">
      {/* Quadrante 1: Lista de Sessões */}
      <div className="quadrant q1-sessions">
        <h3>Sessões do Evento</h3>
        <Quadrant1Sessions
          meetingKey={meetingKey}
          onSessionSelect={handleSessionSelect}
          selectedSessionKey={selectedSessionKey}
        />
      </div>

      {/* Quadrante 2: Lista de Drivers */}
      <div className="quadrant q2-mock">
        <h3>Drivers da Sessão Selecionada</h3>
        {selectedSessionKey ? (
          <DriversList
            sessionKey={selectedSessionKey}
            onDriverSelect={handleDriverSelectForTrackMap}
          />
        ) : (
          <p>Selecione uma sessão para ver os drivers.</p>
        )}
      </div>

      {/* Quadrante 3: Mapa da Pista / Telemetria */}
      <div className="quadrant quadrant-weather">
        <h3>Mapa da Pista / Telemetria</h3>
        {selectedSessionKey && selectedSessionStartDate && selectedDriverForTrackMap ? (
          <TrackMap
            sessionKey={selectedSessionKey}
            startDate={toUTCISOString(selectedSessionStartDate)}
            endDate={toUTCISOString(selectedSessionEndDate)}
            selectedDriver={selectedDriverForTrackMap}
          />
        ) : (
          <p>Selecione uma sessão e clique em um piloto para ver o mapa da pista.</p>
        )}
      </div>

      {/* Quadrante 4: Detalhes da Sessão */}
      <div className="quadrant q4-details">
        <h3>Detalhes da Sessão / Grid de Largada</h3>
        <p>Este quadrante pode exibir mais detalhes da sessão ou o grid de largada.</p>
      </div>
    </div>
  );
}

export default MainContent;
