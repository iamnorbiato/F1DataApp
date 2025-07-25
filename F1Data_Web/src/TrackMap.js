// G:\Learning\F1Data\F1Data_Web\src\TrackMap.js
import React, { useState, useEffect, useRef, useCallback } from 'react';
import Plotly from 'plotly.js/dist/plotly-basic';
import Plot from 'react-plotly.js';
import { API_BASE_URL } from './api';

// MODIFICADO: Função para formatar o tempo como MM:SS.ms (tempo decorrido na sessão)
const formatTimeWithMilliseconds = (dateString) => {
  if (!dateString) return 'N/A';
  const date = new Date(dateString);
  // Para exibir como tempo decorrido, precisamos de um ponto de referência inicial da sessão
  // Como não temos a data de início da *sessão completa* aqui, vamos assumir que o "00:00.00"
  // seria o início do período do primeiro ponto de dados recebido na janela de 20 min.
  // Para simplificar e mostrar apenas MM:SS.ms da data UTC, vamos calcular assim:
  const totalSeconds = date.getUTCMinutes() * 60 + date.getUTCSeconds() + date.getUTCMilliseconds() / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const secondsInt = Math.floor(seconds);
  const milliseconds = Math.floor((seconds - secondsInt) * 1000);

  return `${String(minutes).padStart(2, '0')}:${String(secondsInt).padStart(2, '0')}.${String(milliseconds).padStart(3, '0').substring(0, 2)}`;
};

const formatDateUTCISOString = (date) => date.toISOString();

function TrackMap({ sessionKey, startDate, endDate, selectedDriver }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentTelemetryWindowStart, setCurrentTelemetryWindowStart] = useState(null);
  const [currentPointTime, setCurrentPointTime] = useState(null);
  const [speedMultiplier, setSpeedMultiplier] = useState(1.0);
  // REMOVIDO: debugMessages não é mais um estado, pois não será exibido na tela
  // const [debugMessages, setDebugMessages] = useState([]); 

  const currentWindowTelemetryDataRef = useRef([]);
  const plotDivRef = useRef(null);
  const currentPointIndexRef = useRef(0);
  const animationFrameIdRef = useRef(null);
  const animationStartTimeRef = useRef(performance.now());
  const speedMultiplierValueRef = useRef(1.0);

  const sessionStartDateRef = useRef(null);
  const sessionEndDateRef = useRef(null);

  // REMOVIDO: addDebugMessage não é mais uma função de callback de estado
  // Agora usará console.log diretamente para as mensagens de depuração
  const logDebugMessage = (message) => {
    // Apenas loga no console para mensagens de depuração críticas
    console.log(`TrackMap Debug: ${message}`);
  };

  useEffect(() => {
    if (sessionKey && startDate) {
      const start = new Date(startDate);
      if (!isNaN(start)) {
        setCurrentTelemetryWindowStart(start);
        sessionStartDateRef.current = start;
        logDebugMessage(`Sessão iniciada. Data de início: ${start.toISOString()}`);
      } else {
        logDebugMessage(`Erro: startDate inválida: ${startDate}`);
        console.error('TrackMap: Invalid startDate received:', startDate);
      }
    } else {
      setCurrentTelemetryWindowStart(null);
      sessionStartDateRef.current = null;
      logDebugMessage('Sessão ou Data de Início nula/indefinida.');
    }
    // Não é mais necessário limpar debugMessages aqui
  }, [sessionKey, startDate]);

  useEffect(() => {
    if (endDate) {
      const end = new Date(endDate);
      if (!isNaN(end)) {
        sessionEndDateRef.current = end;
        logDebugMessage(`Data final da sessão definida: ${sessionEndDateRef.current.toISOString()}`);
      } else {
        logDebugMessage(`Erro: endDate inválida: ${endDate}`);
        console.error('TrackMap: Invalid endDate received:', endDate);
        sessionEndDateRef.current = null;
      }
    } else {
      sessionEndDateRef.current = null;
      logDebugMessage('endDate é nula/indefinida.');
    }
  }, [endDate]);

  useEffect(() => {
    if (animationFrameIdRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
      animationFrameIdRef.current = null;
    }

    if (!selectedDriver || !sessionKey || !currentTelemetryWindowStart) {
      setLoading(false);
      currentWindowTelemetryDataRef.current = [];
      if (plotDivRef.current) {
        Plotly.purge(plotDivRef.current);
      }
      return;
    }

    const fetchTelemetryData = async () => {
      setLoading(true);
      setError(null);
      logDebugMessage(`Buscando dados para: ${formatDateUTCISOString(currentTelemetryWindowStart)}`);
      try {
        const url = `${API_BASE_URL}/api/location-by-session-and-driver/?session_key=${sessionKey}&driver_number=${selectedDriver.driver_number}&date=${formatDateUTCISOString(currentTelemetryWindowStart)}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Erro ao buscar telemetria: ${response.status}`);
        const data = await response.json();
        const sorted = data.sort((a, b) => new Date(a.date) - new Date(b.date));
        currentWindowTelemetryDataRef.current = sorted;
        logDebugMessage(`Dados recebidos. Pontos: ${sorted.length}. Preparando animação.`);

        if (plotDivRef.current && sorted.length > 0) {
          const newTrackTrace = {
            x: sorted.map(d => d.x),
            y: sorted.map(d => d.y),
            mode: 'lines',
            name: 'Traçado da Pista',
            line: { color: 'gray', width: 1 },
            hoverinfo: 'none'
          };
          const initialPoint = sorted[0];
          const newCarPositionTrace = {
            x: initialPoint ? [initialPoint.x] : [],
            y: initialPoint ? [initialPoint.y] : [],
            mode: 'markers',
            marker: { size: 10, color: 'red' },
            name: 'Carro',
            hoverinfo: 'x+y+text',
            text: initialPoint ? [`Driver: ${selectedDriver?.full_name || selectedDriver?.driver_number}<br>Time: ${formatTimeWithMilliseconds(initialPoint.date)}`] : []
          };
          
          Plotly.react(plotDivRef.current, [newTrackTrace, newCarPositionTrace], getLayout());
        }

      } catch (err) {
        setError(err.message || 'Erro ao carregar telemetria.');
        logDebugMessage(`Erro ao buscar telemetria: ${err.message}`);
        console.error('TrackMap: Error fetching telemetry:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchTelemetryData();
  }, [sessionKey, selectedDriver, currentTelemetryWindowStart, API_BASE_URL]);

  useEffect(() => {
    if (currentWindowTelemetryDataRef.current.length < 2 || !plotDivRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
      return;
    }

    currentPointIndexRef.current = 0;
    animationStartTimeRef.current = performance.now();

    const animateCar = (timestamp) => {
      const data = currentWindowTelemetryDataRef.current;
      if (!plotDivRef.current || data.length === 0) {
        animationFrameIdRef.current = null;
        return;
      }

      const elapsed = timestamp - animationStartTimeRef.current;
      const basePointsPerSecond = 5;
      const effectivePointsPerSecond = basePointsPerSecond * speedMultiplierValueRef.current;

      if (effectivePointsPerSecond <= 0) {
        animationFrameIdRef.current = requestAnimationFrame(animateCar);
        return;
      }

      const index = Math.floor(elapsed / (1000 / effectivePointsPerSecond));

      if (index >= data.length) {
        const WINDOW_MILLISECONDS = 20 * 60 * 1000;
        let newStart = null;

        if (currentTelemetryWindowStart) {
          newStart = new Date(currentTelemetryWindowStart.getTime() + WINDOW_MILLISECONDS);
        }

        if (newStart && sessionEndDateRef.current && newStart <= sessionEndDateRef.current) {
          console.log(`TrackMap Debug: AVANÇANDO! Próxima data de API: ${newStart.toISOString()}`);
          setCurrentTelemetryWindowStart(newStart);
        } else {
          // Mensagens de FINALIZAÇÃO para o CONSOLE
          console.log('TrackMap Debug: !!! ANIMAÇÃO FINALIZADA !!!');
          if (!newStart) console.log('TrackMap Debug: Motivo: Próxima Janela é nula.');
          if (!sessionEndDateRef.current) console.log('TrackMap Debug: Motivo: Data Final da Sessão é nula.');
          if (newStart && sessionEndDateRef.current && newStart > sessionEndDateRef.current) {
            console.log(`TrackMap Debug: Motivo: Próxima Janela (${newStart.toISOString()}) ultrapassou Data Final da Sessão (${sessionEndDateRef.current.toISOString()}).`);
          }
        }
        return;
      }

      const point = data[index];
      if (point) {
        setCurrentPointTime(point.date);
        try {
          Plotly.restyle(plotDivRef.current, {
            x: [[point.x]],
            y: [[point.y]],
            text: [[`Driver: ${selectedDriver?.full_name || selectedDriver?.driver_number}<br>Time: ${formatTimeWithMilliseconds(point.date)}`]]
          }, [1]);
        } catch (err) {
          console.warn("TrackMap: Erro ao animar ponto (restyle):", err);
        }
      }

      animationFrameIdRef.current = requestAnimationFrame(animateCar);
    };

    cancelAnimationFrame(animationFrameIdRef.current);
    animationFrameIdRef.current = requestAnimationFrame(animateCar);

    return () => cancelAnimationFrame(animationFrameIdRef.current);
  }, [currentWindowTelemetryDataRef.current, selectedDriver, currentTelemetryWindowStart]);

  const handleSlower = () => {
    setSpeedMultiplier(prev => {
      const newSpeed = Math.max(0.1, prev * 0.5);
      speedMultiplierValueRef.current = newSpeed;
      return newSpeed;
    });
  };

  const handleFaster = () => {
    setSpeedMultiplier(prev => {
      const newSpeed = Math.min(10.0, prev * 1.5);
      speedMultiplierValueRef.current = newSpeed;
      return newSpeed;
    });
  };

  const getLayout = useCallback(() => ({
    autosize: true,
    margin: { l: 20, r: 20, b: 20, t: 20 },
    xaxis: { showgrid: false, zeroline: false, visible: false, scaleanchor: "y", scaleratio: 1 },
    yaxis: { showgrid: false, zeroline: false, visible: false, scaleanchor: "x", scaleratio: 1 },
    plot_bgcolor: 'transparent',
    paper_bgcolor: 'transparent',
    showlegend: false,
    hovermode: 'closest',
  }), []);

  if (loading) return <p>Carregando mapa da pista...</p>;
  if (!selectedDriver) return <p>Nenhum piloto selecionado para o mapa da pista.</p>;
  if (error) return <p style={{ color: 'red' }}>Erro: {error}</p>;
  if (selectedDriver && currentWindowTelemetryDataRef.current.length === 0 && currentTelemetryWindowStart && sessionEndDateRef.current && currentTelemetryWindowStart < sessionEndDateRef.current)
    return <p>Carregando dados de telemetria para o driver {selectedDriver.full_name || selectedDriver.driver_number}...</p>;
  if (selectedDriver && currentWindowTelemetryDataRef.current.length === 0)
    return <p>Nenhum dado de telemetria encontrado para o driver {selectedDriver.full_name || selectedDriver.driver_number} ou fim da sessão alcançado.</p>;

  const initialPoint = currentWindowTelemetryDataRef.current[0];
  const initialDataForPlot = [
    {
      x: currentWindowTelemetryDataRef.current.map(d => d.x),
      y: currentWindowTelemetryDataRef.current.map(d => d.y),
      mode: 'lines',
      name: 'Traçado da Pista',
      line: { color: 'gray', width: 1 },
      hoverinfo: 'none'
    },
    {
      x: initialPoint ? [initialPoint.x] : [],
      y: initialPoint ? [initialPoint.y] : [],
      mode: 'markers',
      marker: { size: 10, color: 'red' },
      name: 'Carro',
      hoverinfo: 'x+y+text',
      text: initialPoint ? [`Driver: ${selectedDriver?.full_name || selectedDriver?.driver_number}<br>Time: ${formatTimeWithMilliseconds(initialPoint.date)}`] : []
    }
  ];

  return (
    <div className="track-map-container drivers-list-panel">
      <h2 className="panel-title">Movimentação na Pista: {selectedDriver?.full_name || `Driver ${selectedDriver?.driver_number}`}</h2>

      {/* REMOVIDO: Div para mensagens de debug na tela */}

      <Plot
        data={initialDataForPlot}
        layout={getLayout()}
        config={{ displayModeBar: false, responsive: true }}
        useResizeHandler={true}
        style={{ width: '100%', height: '100%' }}
        onInitialized={(figure, graphDiv) => { plotDivRef.current = graphDiv; }}
        onUpdate={(figure, graphDiv) => { plotDivRef.current = graphDiv; }}
      />

      <div className="track-map-controls">
        <button className="track-map-nav-button" onClick={handleSlower}>&lt;&lt;&lt;</button>
        {/* RESTAURADO COM FORMATO AJUSTADO: Span para exibir o tempo do ponto atual da animação (currentPointTime) */}
        <span className="track-map-current-time">
          {currentPointTime
            ? formatTimeWithMilliseconds(currentPointTime) // Chama a função ajustada
            : '00:00.00'}
        </span>
        <button className="track-map-nav-button" onClick={handleFaster}>&gt;&gt;&gt;</button>
      </div>
    </div>
  );
}

export default TrackMap;