// G:\Learning\F1Data\F1Data_Web\src\SessionResultsPanel.js

import React, { useState, useEffect } from 'react';

// --- FUNÇÕES DE FORMATAÇÃO E AJUSTE ---

// Função para formatar segundos em MM:SS.ms (para Practice e Q1/Q2/Q3)
const formatSecondsToMinutesSeconds = (totalSeconds) => {
  if (totalSeconds === null) {
    return 'N/A';
  }
  const numSeconds = Number(totalSeconds); 
  if (isNaN(numSeconds)) { 
    return 'N/A';
  }

  const minutes = Math.floor(numSeconds / 60);
  const seconds = numSeconds % 60;
  const secondsInt = Math.floor(seconds);
  const milliseconds = Math.floor((seconds - secondsInt) * 1000);

  return `${String(minutes).padStart(1, '0')}:${String(secondsInt).padStart(2, '0')}.${String(milliseconds).padStart(3, '0')}`;
};

// NOVA FUNÇÃO: Formatar segundos em HH:MM:SS.ms (para Race)
const formatTotalRaceDuration = (totalSeconds) => {
  if (totalSeconds === null) {
    return 'N/A';
  }
  const numSeconds = Number(totalSeconds);
  if (isNaN(numSeconds)) {
    return 'N/A';
  }

  const hours = Math.floor(numSeconds / 3600);
  const remainingSeconds = numSeconds % 3600;
  const minutes = Math.floor(remainingSeconds / 60);
  const seconds = remainingSeconds % 60;
  const secondsInt = Math.floor(seconds);
  const milliseconds = Math.floor((seconds - secondsInt) * 1000);

  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secondsInt).padStart(2, '0')}.${String(milliseconds).padStart(3, '0')}`;
};

// Função para formatar gap_to_leader (usará as funções acima internamente)
const formatGapToLeaderValue = (gapArray, sessionType) => { 
  if (!gapArray || gapArray.length === 0 || gapArray[0] === null) {
    return 'N/A';
  }

  const value = gapArray[0];

  if (typeof value === 'string') {
    if (value.includes('LAP')) {
      return value;
    }
    try {
      const numValue = Number(value); 
      if (!isNaN(numValue)) {
        return formatSecondsToMinutesSeconds(numValue); 
      }
    } catch (e) {}
    return 'N/A';
  } else if (typeof value === 'number') {
    return formatSecondsToMinutesSeconds(value); 
  }
  return 'N/A';
};

// NOVA FUNÇÃO: Formatar tempo para Qualificação (Q1, Q2, Q3)
const formatQualifyingTime = (timeInSeconds) => {
  if (timeInSeconds === null || isNaN(timeInSeconds)) {
    return 'DNQ'; // Se não tiver tempo, é "Did Not Qualify"
  }
  return formatSecondsToMinutesSeconds(timeInSeconds); // Reutiliza a formatação M:SS.ms
};

// Consolida DNF/DNS/DSQ em um único status
const getCombinedStatusFlag = (item) => {
  if (item.dsq) return 'DSQ';
  if (item.dnf) return 'DNF';
  if (item.dns) return 'DNS';
  return null;
};

// --- FIM DAS FUNÇÕES DE FORMATAÇÃO E AJUSTE ---


function SessionResultsPanel({ sessionKey }) {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [hoveredHeadshotUrl, setHoveredHeadshotUrl] = useState(null);
  const [mouseX, setMouseX] = useState(0);
  const [mouseY, setMouseY] = useState(0);

  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://home:30008'; 

  useEffect(() => {
    const fetchSessionResults = async () => {
      if (!sessionKey) {
        setResults([]);
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const response = await fetch(`${API_BASE_URL}/api/session-results-by-session/?session_key=${sessionKey}`);
        if (!response.ok) {
          throw new Error(`Erro ao buscar resultados da sessão: ${response.status} ${response.statusText}`);
        }
        const data = await response.json();

        // --- LÓGICA DE ORDENAÇÃO DOS RESULTADOS (INALETARADA) ---
        const sortedData = [...data].sort((a, b) => {
          const statusA = getCombinedStatusFlag(a);
          const statusB = getCombinedStatusFlag(b);

          const getStatusWeight = (status) => {
            if (status === 'DNS') return 3; 
            if (status === 'DNF') return 2; 
            if (status === 'DSQ') return 4; 
            return 1; // Posição normal
          };

          const weightA = getStatusWeight(statusA);
          const weightB = getStatusWeight(statusB);

          if (weightA !== weightB) {
            return weightA - weightB;
          }

          if (statusA === 'DNF' && statusB === 'DNF') {
            const lapsA = a.number_of_laps || 0;
            const lapsB = b.number_of_laps || 0;
            return lapsB - lapsA; 
          }
          
          const posA = parseInt(a.position);
          const posB = parseInt(b.position);

          if (!isNaN(posA) && !isNaN(posB)) {
            return posA - posB;
          }
          return 0; 
        });
        // --- FIM DA LÓGICA DE ORDENAÇÃO ---

        setResults(sortedData || []); 

      } catch (err) {
        console.error("Geni: Erro ao carregar resultados da sessão:", err);
        setError(err.message || 'Erro ao carregar resultados.');
      } finally {
        setLoading(false);
      }
    };

    fetchSessionResults();
  }, [sessionKey, API_BASE_URL]);

  const handleMouseEnter = (url) => {
    setHoveredHeadshotUrl(url);
  };

  const handleMouseLeave = () => {
    setHoveredHeadshotUrl(null);
  };

  const handleMouseMove = (event) => {
    setMouseX(event.clientX);
    setMouseY(event.clientY);
  };

  if (loading) {
    return (
      <div className="session-results-panel">
        <h2>Resultado da Sessão</h2>
        <p>Carregando resultados...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="session-results-panel">
        <h2>Resultado da Sessão</h2>
        <p style={{ color: 'red' }}>Erro: {error}</p>
      </div>
    );
  }

  if (results.length === 0) {
    return (
      <div className="session-results-panel">
        <h2>Resultado da Sessão</h2>
        <p>Nenhum resultado encontrado para esta sessão.</p>
      </div>
    );
  }

  const currentSessionType = results.length > 0 ? results[0].session_type : null;
  const isPractice = (currentSessionType === 'Practice');
  const isRace = (currentSessionType === 'Race');
  const isQualifying = (currentSessionType === 'Qualifying'); 

  return (
    <div className="session-results-panel">
      <h2>Resultado da Sessão</h2>
        {/* CABEÇALHO DA TABELA - FIXO E CONDICIONAL */}
        {currentSessionType && ( 
          <div className={`session-results-table-header ${isQualifying ? 'qualifying-header-layout' : 'practice-race-header-layout'}`}> 
              <span className="header-pos">Pos</span>
              <span className="header-pilot">Driver & Team</span>
              
              {/* CABEÇALHO CONDICIONAL PARA LAPS / Q1 / Q2 / Q3 */}
              {(isPractice || isRace) && <span className="header-laps">Laps</span>}
              {isQualifying && <span className="header-q3">Q3</span>}
              {isQualifying && <span className="header-pos-q2">Pos Q2</span>}
              {isQualifying && <span className="header-q2">Q2</span>}
              {isQualifying && <span className="header-pos-q1">Pos Q1</span>}
              {isQualifying && <span className="header-q1">Q1</span>}


              {/* CABEÇALHO CONDICIONAL "BEST" / "DURATION" */}
              {isPractice && <span className="header-best">Best</span>}
              {isRace && <span className="header-duration-race">Duration</span>}

              {/* CORRIGIDO: CABEÇALHO "GAP" SÓ PARA PRACTICE/RACE */}
              {!(isQualifying) && <span className="header-gap">Gap</span>} 
              
              {(isPractice || isRace) && <span className="header-status">Status</span>} 
          </div>
        )}

        <div
          className="session-results-content"
          onMouseMove={handleMouseMove}
        >
          <ul className="session-results-list">
            {results.map((item, index) => {
              const sessionType = item.session_type; 
              const statusFlag = getCombinedStatusFlag(item); 

              let displayedPosition = item.position;
              if (isNaN(parseInt(item.position)) || item.position === null || statusFlag) {
                displayedPosition = index + 1; 
              }

              const positionClass = `position ${
                item.position === 'DQ' || item.position === 'NC' || statusFlag ? 'position-status-like' : ''
              }`;

              return (
                <li
                  key={item.driver_number || index} 
                  className={`session-results-item ${isQualifying ? 'qualifying-layout' : 'practice-race-layout'}`} 
                  onMouseEnter={() => handleMouseEnter(item.headshot_url)}
                  onMouseLeave={handleMouseLeave}
                >
                  {/* RENDERIZAÇÃO DE ITENS PARA PRACTICE OU RACE */}
                  {(isPractice || isRace) && (
                    <>
                      <span className={positionClass}>{displayedPosition}</span>
                      <span className="pilot">{item.broadcast_name} ({item.team_name})</span>
                      <span className="number-of-laps">{item.number_of_laps || '-'}</span> 
                      
                      {/* APENAS O VALOR FORMATADO DA DURAÇÃO (COM 3 DÍGITOS PARA MS) */}
                      <span className="duration">
                        {sessionType === 'Practice' && formatSecondsToMinutesSeconds(item.duration && item.duration[0])}
                        {sessionType === 'Race' && formatTotalRaceDuration(item.duration && item.duration[0])}
                      </span> 

                      <span className="gap">{formatGapToLeaderValue(item.gap_to_leader, sessionType)}</span>
                      
                      <span className="status-flags-container">
                        {statusFlag && <span className="status-flag">{statusFlag}</span>}
                      </span>
                    </>
                  )}

                  {/* RENDERIZAÇÃO DE ITENS PARA QUALIFYING */}
                  {isQualifying && (
                      <>
                        <span className={positionClass}>{displayedPosition}</span> 
                        <span className="pilot">{item.broadcast_name} ({item.team_name})</span>
                        <span className="q-time q3-time">{formatQualifyingTime(item.duration && item.duration[0])}</span>
                        <span className="pos-q2">{item.pos_q2 || '-'}</span> 
                        <span className="q-time q2-time">{formatQualifyingTime(item.duration && item.duration[1])}</span>
                        <span className="pos-q1">{item.pos_q1 || '-'}</span> 
                        <span className="q-time q1-time">{formatQualifyingTime(item.duration && item.duration[2])}</span>
                      </>
                  )}

                  {/* PLACEHOLDER PARA OUTROS TIPOS DE SESSÃO NÃO TRATADOS */}
                  {!(isPractice || isRace || isQualifying) && (
                      <span className="full-row-item">
                        {item.broadcast_name} - Posição: {displayedPosition} (Tipo de Sessão: {sessionType})
                      </span>
                  )}
                </li>
              );
            })}
          </ul>
          {/* Renderização da imagem de hover */}
          {hoveredHeadshotUrl && (
            <div className="headshot-hover-container">
              <img
                src={hoveredHeadshotUrl}
                alt="Piloto"
                className="headshot-hover-image"
                style={{
                  position: 'fixed',
                  left: mouseX + 15 + 'px',
                  top: mouseY + 15 + 'px',
                  maxWidth: '120px',
                  maxHeight: '120px',
                  border: '1px solid var(--border-color)',
                  borderRadius: '5px',
                  boxShadow: '2px 2px 5px rgba(0, 0, 0, 0.3)',
                  zIndex: 1000,
                  pointerEvents: 'none',
                }}
              />
            </div>
          )}
        </div>
    </div>
  );
}

export default SessionResultsPanel;