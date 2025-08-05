// G:\Learning\F1Data\F1Data_Web\src\TrackMap.js
import React, { useState, useEffect, useRef, useCallback } from 'react';
import Plotly from 'plotly.js/dist/plotly-basic';
import Plot from 'react-plotly.js';
import { API_BASE_URL } from './api'; // ajuste o caminho se necessário
console.log('API_BASE_URL:', API_BASE_URL);


const WINDOW_MILLISECONDS = 20 * 60 * 1000; // 20 minutos em milissegundos

// Funções de formatação de data
const formatTimeWithMilliseconds = (dateString) => {
  if (!dateString) return 'N/A';
  const date = new Date(dateString);
  if (isNaN(date.getTime())) {
    console.error('formatTimeWithMilliseconds: Invalid date string:', dateString);
    return 'N/A';
  }
  
  const totalSeconds = date.getUTCMinutes() * 60 + date.getUTCSeconds() + date.getUTCMilliseconds() / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const secondsInt = Math.floor(seconds);
  const milliseconds = Math.floor((seconds - secondsInt) * 1000);

  return `${String(minutes).padStart(2, '0')}:${String(secondsInt).padStart(2, '0')}.${String(milliseconds).padStart(3, '0').substring(0, 2)}`;
};

const formatDateForDjango = (date) => {
  let isoString = date.toISOString();
  isoString = isoString.replace('Z', '').replace(/\.\d+$/, '');
  
  if (date.getUTCMilliseconds() > 0) {
    isoString += '.' + String(date.getUTCMilliseconds()).padStart(3, '0');
  }

  return isoString.replace('T', ' ');
};


function formatMillisecondsToMMSSmmm(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const milliseconds = ms % 1000;

  return (
    String(minutes).padStart(2, '0') +
    ':' +
    String(seconds).padStart(2, '0') +
    '.' +
    String(milliseconds).padStart(3, '0')
  );
}    


function TrackMap({ sessionKey, selectedDriver }) { 
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentTelemetryWindowStart, setCurrentTelemetryWindowStart] = useState(null);
  const [currentPointTime, setCurrentPointTime] = useState(null);
  const [speedMultiplier, setSpeedMultiplier] = useState(1.0);
  const [dataAvailable, setDataAvailable] = useState(true);

  const currentWindowTelemetryDataRef = useRef([]);
  const plotDivRef = useRef(null);
  const currentPointIndexRef = useRef(0);
  const animationFrameIdRef = useRef(null);
  const animationStartTimeRef = useRef(performance.now());
  const speedMultiplierValueRef = useRef(1.0);

  const locationMinDateRef = useRef(null);
  const locationMaxDateRef = useRef(null);
  const telemetryDataFetching = useRef(false);

  // Função auxiliar para calcular tempo decorrido da sessão em segundos
  const sessionDurationSeconds = locationMaxDateRef.current && locationMinDateRef.current
    ? (locationMaxDateRef.current.getTime() - locationMinDateRef.current.getTime()) / 1000
    : 0;

  // Função para formatar segundos em mm:ss
  const formatSecondsToMMSS = (totalSeconds) => {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = Math.floor(totalSeconds % 60);
    return `${String(minutes).padStart(2,'0')}:${String(seconds).padStart(2,'0')}`;
  };

  // Recalcula animationStartTimeRef para manter a posição atual ao mudar velocidade
  const adjustAnimationStartTime = (newSpeed) => {
    const now = performance.now();
    const elapsedRealTime = now - animationStartTimeRef.current;
    const elapsedAnimationTime = elapsedRealTime * speedMultiplierValueRef.current; // tempo virtual da animação antes da mudança
    animationStartTimeRef.current = now - (elapsedAnimationTime / newSpeed);
    speedMultiplierValueRef.current = newSpeed;
    setSpeedMultiplier(newSpeed);
  };
  
  
  const logDebugMessage = (message) => {
    console.log(`TrackMap Debug: ${message}`);
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
  
  const fetchTelemetryData = useCallback(async (start_date) => {
    if (telemetryDataFetching.current) return;
    
    telemetryDataFetching.current = true;
    setLoading(true);
    setError(null);
    
    const currentTelemetryWindowEnd = new Date(start_date.getTime() + WINDOW_MILLISECONDS);

  try {
    const url = `${API_BASE_URL}/api/location-by-session-and-driver/?session_key=${sessionKey}&driver_number=${selectedDriver.driver_number}&date__gte=${formatDateForDjango(start_date)}&date__lt=${formatDateForDjango(currentTelemetryWindowEnd)}`;

    console.log('DEBUG TrackMap.js: URL da API de localização:', url);
    const response = await fetch(url);
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Erro ao buscar telemetria: ${response.status} - ${errorText}`);
    }
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

      // AQUI: se for a primeira vez, cria novo gráfico
      if (!plotDivRef.current.data || plotDivRef.current.data.length === 0) {
        Plotly.newPlot(plotDivRef.current, [newTrackTrace, newCarPositionTrace], getLayout());
      } else {
        // se gráfico já existe, atualiza só os dados sem recriar o gráfico (sem flicker)
        Plotly.restyle(plotDivRef.current, {
          x: [newTrackTrace.x],
          y: [newTrackTrace.y]
        }, [0]); // atualiza a linha da pista (trace 0)

        Plotly.restyle(plotDivRef.current, {
          x: [newCarPositionTrace.x],
          y: [newCarPositionTrace.y],
          text: [newCarPositionTrace.text]
        }, [1]); // atualiza posição inicial do carro (trace 1)
      }
    } else {
      if (currentTelemetryWindowEnd < locationMaxDateRef.current) {
        setCurrentTelemetryWindowStart(currentTelemetryWindowEnd);
      }
    }

  } catch (err) {
    setError(err.message || 'Erro ao carregar telemetria.');
    logDebugMessage(`Erro ao buscar telemetria: ${err.message}`);
    console.error('TrackMap: Error fetching telemetry:', err);
  } finally {
    setLoading(false);
    telemetryDataFetching.current = false;
  }
  
/*AQUI
    try {
      const url = `${API_BASE_URL}/api/location-by-session-and-driver/?session_key=${sessionKey}&driver_number=${selectedDriver.driver_number}&date__gte=${formatDateForDjango(start_date)}&date__lt=${formatDateForDjango(currentTelemetryWindowEnd)}`;
      
      console.log('DEBUG TrackMap.js: URL da API de localização:', url);
      const response = await fetch(url);
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Erro ao buscar telemetria: ${response.status} - ${errorText}`);
      }
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
      } else {
        if (currentTelemetryWindowEnd < locationMaxDateRef.current) {
          setCurrentTelemetryWindowStart(currentTelemetryWindowEnd);
        }
      }

    } catch (err) {
      setError(err.message || 'Erro ao carregar telemetria.');
      logDebugMessage(`Erro ao buscar telemetria: ${err.message}`);
      console.error('TrackMap: Error fetching telemetry:', err);
    } finally {
      setLoading(false);
      telemetryDataFetching.current = false;
    }
AQUI*/
  }, [sessionKey, selectedDriver, currentTelemetryWindowStart, getLayout]);


  const animateCar = useCallback((timestamp) => {
    const data = currentWindowTelemetryDataRef.current;
    if (!plotDivRef.current || data.length === 0) {
      cancelAnimationFrame(animationFrameIdRef.current);
      animationFrameIdRef.current = null;
      if (currentTelemetryWindowStart && locationMaxDateRef.current && currentTelemetryWindowStart < locationMaxDateRef.current) {
        setCurrentTelemetryWindowStart(new Date(currentTelemetryWindowStart.getTime() + WINDOW_MILLISECONDS));
      } else {
        logDebugMessage('Nenhum dado no último chunk. Animação finalizada.');
        setDataAvailable(false);
      }
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
      let newStart = new Date(currentTelemetryWindowStart.getTime() + WINDOW_MILLISECONDS);

      if (newStart <= locationMaxDateRef.current) {
        logDebugMessage(`AVANÇANDO! Próxima data de API: ${newStart.toISOString()}`);
        setCurrentTelemetryWindowStart(newStart);
      } else {
        logDebugMessage('!!! ANIMAÇÃO FINALIZADA !!!');
        setDataAvailable(false);
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
  }, [currentTelemetryWindowStart, locationMaxDateRef, selectedDriver, speedMultiplierValueRef]);


  useEffect(() => {
    if (!selectedDriver || !sessionKey) {
      setDataAvailable(true);
      return;
    }

    const fetchMinMaxDates = async () => {
        setLoading(true);
        setError(null);
        try {
            console.log(`Buscando datas MIN/MAX para Sessão ${sessionKey}, Driver ${selectedDriver.driver_number}...`);
            const url = `${API_BASE_URL}/api/min-max-location-date/?session_key=${sessionKey}&driver_number=${selectedDriver.driver_number}`;
            const response = await fetch(url);
            if (!response.ok) {
              const errorText = await response.text();
              throw new Error(`Erro ao buscar datas Min/Max: ${response.status} - ${errorText}`);
            }
            const data = await response.json();
            if (data.min_date && data.max_date) {
                const min_date_obj = new Date(data.min_date);
                const max_date_obj = new Date(data.max_date);
                locationMinDateRef.current = min_date_obj;
                locationMaxDateRef.current = max_date_obj;
                setCurrentTelemetryWindowStart(min_date_obj);
                console.log(`Datas MIN/MAX de location recebidas: MIN=${data.min_date}, MAX=${data.max_date}`);
            } else {
                console.warn(`WARN: Nenhum dado de localização encontrado para Sessão ${sessionKey}, Driver ${selectedDriver.driver_number}.`);
                setDataAvailable(false);
            }
        } catch (error) {
            console.error("ERROR: Erro ao buscar as datas MIN/MAX da Location:", error);
            setError(error.message || 'Erro ao carregar datas de telemetria.');
            setDataAvailable(false);
        } finally {
            setLoading(false);
        }
    };
    fetchMinMaxDates();
  }, [sessionKey, selectedDriver]);


  useEffect(() => {
    if (currentTelemetryWindowStart && dataAvailable && !telemetryDataFetching.current) {
        fetchTelemetryData(currentTelemetryWindowStart);
    }
  }, [currentTelemetryWindowStart, dataAvailable, fetchTelemetryData]);


  useEffect(() => {
    if (animationFrameIdRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
    }

    if (currentWindowTelemetryDataRef.current.length > 1 && plotDivRef.current) {
        animationStartTimeRef.current = performance.now();
        animationFrameIdRef.current = requestAnimationFrame(animateCar);
    }

    return () => cancelAnimationFrame(animationFrameIdRef.current);
  }, [currentWindowTelemetryDataRef.current, animateCar]);

  const handleSlower = () => {
    setSpeedMultiplier(prev => {
      const newSpeed = Math.max(0.1, prev * 0.5);
      adjustAnimationStartTime(newSpeed);
      return newSpeed; // setSpeedMultiplier será chamado dentro de adjustAnimationStartTime, mas mantemos pra React
    });
  };

  const handleFaster = () => {
    setSpeedMultiplier(prev => {
      const newSpeed = Math.min(10.0, prev * 1.5);
      adjustAnimationStartTime(newSpeed);
      return newSpeed;
    });
  };

  // Para mostrar o tempo decorrido da sessão entre os botões no render:
  const elapsedSeconds = (() => {
    if (!animationStartTimeRef.current || !performance.now()) return 0;
    const now = performance.now();
    const elapsedRealTime = now - animationStartTimeRef.current;
    return Math.floor(elapsedRealTime * speedMultiplierValueRef.current);
  })();

  const elapsedMilliseconds = (() => {
    if (!animationStartTimeRef.current || !performance.now()) return 0;
    const now = performance.now();
    const elapsedRealTime = now - animationStartTimeRef.current;
    return Math.floor(elapsedRealTime * speedMultiplierValueRef.current);
  })();

  const displayElapsedTime = locationMinDateRef.current
  ? formatMillisecondsToMMSSmmm(elapsedMilliseconds)
  : '00:00.000';

  const sessionDurationMs = locationMaxDateRef.current && locationMinDateRef.current
    ? locationMaxDateRef.current.getTime() - locationMinDateRef.current.getTime()
    : 0;
      
  const displaySessionDuration = sessionDurationMs > 0
    ? formatMillisecondsToMMSSmmm(sessionDurationMs)
    : '00:00.000';
    
  if (loading) return <p>Carregando mapa da pista...</p>;
  if (!selectedDriver) return <p>Nenhum piloto selecionado para o mapa da pista.</p>;
  if (error) return <p style={{ color: 'red' }}>Erro: {error}</p>;
  if (!dataAvailable) return <p>Nenhum dado de telemetria encontrado para o driver {selectedDriver.full_name || selectedDriver.driver_number} ou fim da sessão alcançado.</p>;


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
    <div className="telemetry-display-panel">
      <h2 className="panel-title">Movimentação na Pista: {selectedDriver?.full_name || `Driver ${selectedDriver?.driver_number}`}</h2>

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
        <button className="track-map-nav-button" onClick={handleSlower}>Slow</button>
        <span className="track-map-current-time">
          
          {displayElapsedTime} / {displaySessionDuration}
            
        </span>
        <button className="track-map-nav-button" onClick={handleFaster}>Fast</button>
      </div>
    </div>
  );
}

export default TrackMap;