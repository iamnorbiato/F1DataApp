// G:\Learning\F1Data\F1Data_Web\src\MainContent.js
import React, { useState, useEffect } from 'react';
import Quadrant1Sessions from './Quadrant1Sessions';
import DriversList from './DriversList';
import TrackMap from './TrackMap';

function MainContent({ meetingKey }) {
  const [selectedSessionKey, setSelectedSessionKey] = useState(null);
  const [selectedSessionName, setSelectedSessionName] = useState('');
  // NOVO: Estado para armazenar a data de início da sessão
  const [selectedSessionStartDate, setSelectedSessionStartDate] = useState(null);

  // Efeito para resetar a sessão e data de início quando o meetingKey mudar
  useEffect(() => {
    setSelectedSessionKey(null);
    setSelectedSessionName('');
    setSelectedSessionStartDate(null); // NOVO: Reseta a data de início também
  }, [meetingKey]);

  // MODIFICADO: Agora recebe a startDate da sessão
  const handleSessionSelect = (sessionKey, sessionName, startDate) => {
    setSelectedSessionKey(sessionKey);
    setSelectedSessionName(sessionName);
    setSelectedSessionStartDate(startDate); // NOVO: Atualiza o estado com a data de início
    console.log(`MainContent: Sessão selecionada - Key: ${sessionKey}, Nome: ${sessionName}, Data Início: ${startDate}`);
  };

  if (!meetingKey) {
    return (
        <div className="welcome-message">
            <p>Nenhum evento selecionado. Por favor, selecione um no menu.</p>
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
          <DriversList sessionKey={selectedSessionKey} />
        ) : (
          <p>Selecione uma sessão para ver os drivers.</p>
        )}
      </div>

      {/* Quadrante 3: Mapa da Pista / Telemetria */}
      <div className="quadrant quadrant-weather">
        <h3>Mapa da Pista / Telemetria</h3>
        {selectedSessionKey && selectedSessionStartDate ? ( // NOVO: Só renderiza se tiver a data de início
          <TrackMap
            sessionKey={selectedSessionKey}
            startDate={selectedSessionStartDate} // NOVO: Passa a data de início da sessão
          />
        ) : (
          <p>Selecione uma sessão para ver o mapa da pista.</p>
        )}
      </div>

      {/* Quadrante 4: Detalhes da Sessão / Grid de Largada */}
      <div className="quadrant q4-details">
        <h3>Detalhes da Sessão / Grid de Largada</h3>
        <p>Este quadrante pode exibir mais detalhes da sessão ou o grid de largada.</p>
      </div>
    </div>
  );
}

export default MainContent;