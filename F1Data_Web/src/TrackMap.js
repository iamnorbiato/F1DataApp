// G:\Learning\F1Data\F1Data_Web\src\TrackMap.js
import React, { useState, useEffect, useRef } from 'react';
import Plotly from 'plotly.js/dist/plotly-basic';
import Plot from 'react-plotly.js';

function TrackMap({ sessionKey, startDate }) {
  const [telemetryData, setTelemetryData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedDriver, setSelectedDriver] = useState(null);
  const [allDrivers, setAllDrivers] = useState([]); // Mantido para referência, mas não usado para dropdown aqui
  const [currentTelemetryWindowStart, setCurrentTelemetryWindowStart] = useState(null);
  const [currentPointTime, setCurrentPointTime] = useState(null);
  
  // Estado para o multiplicador de velocidade da animação
  const [speedMultiplier, setSpeedMultiplier] = useState(1.0); 
  // Ref para armazenar o valor da velocidade para o loop de animação (não triggera re-render)
  const speedMultiplierValueRef = useRef(1.0); 

  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:30080';

  const animationFrameIdRef = useRef(null);
  const currentPointIndexRef = useRef(0);
  const plotDivRef = useRef(null);
  const animationStartTimeRef = useRef(performance.now());

  // Efeito 1: Inicializa e reseta currentTelemetryWindowStart
  useEffect(() => {
    if (sessionKey && startDate) {
      setCurrentTelemetryWindowStart(new Date(startDate));
    } else {
      setCurrentTelemetryWindowStart(null);
    }
  }, [sessionKey, startDate]);

  // Efeito 2: Busca TODOS os drivers da sessão (para seleção padrão Driver 1)
  useEffect(() => {
    const fetchDrivers = async () => {
      if (!sessionKey) {
        setAllDrivers([]);
        setSelectedDriver(null);
        setTelemetryData([]);
        setCurrentTelemetryWindowStart(null);
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(`${API_BASE_URL}/api/drivers-by-session/?session_key=${sessionKey}`);
        if (!response.ok) throw new Error(`Erro ao buscar drivers: ${response.status}`);
        const drivers = await response.json();
        setAllDrivers(drivers);
        // Sempre seleciona o primeiro driver da lista
        setSelectedDriver(drivers[0] || null); 
      } catch (err) {
        setError(err.message || 'Erro ao carregar drivers.');
      } finally {
        setLoading(false);
      }
    };
    fetchDrivers();
  }, [sessionKey, API_BASE_URL]);

  // Efeito 3: Busca os dados de telemetria para a janela de tempo e driver selecionados
  useEffect(() => {
    const fetchTelemetry = async () => {
      if (!selectedDriver || !sessionKey || !currentTelemetryWindowStart) {
        setTelemetryData([]);
        setLoading(false);
        if (animationFrameIdRef.current) {
          cancelAnimationFrame(animationFrameIdRef.current);
          animationFrameIdRef.current = null;
        }
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const url = `${API_BASE_URL}/api/location-by-session-and-driver/?session_key=${sessionKey}&driver_number=${selectedDriver.driver_number}&date=${currentTelemetryWindowStart.toISOString()}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Erro ao buscar telemetria: ${response.status}`);
        const data = await response.json();
        const sorted = data.sort((a, b) => new Date(a.date) - new Date(b.date));
        setTelemetryData(sorted);
      } catch (err) {
        setError(err.message || 'Erro ao carregar telemetria.');
      } finally {
        setLoading(false);
      }
    };
    fetchTelemetry();
  }, [sessionKey, selectedDriver, API_BASE_URL, currentTelemetryWindowStart]);

  // ANIMAÇÃO COM requestAnimationFrame
  useEffect(() => {
    if (telemetryData.length < 2 || !plotDivRef.current) {
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
        animationFrameIdRef.current = null;
      }
      return;
    }

    currentPointIndexRef.current = 0; // reseta índice ao receber novos dados
    animationStartTimeRef.current = performance.now();

    const animateCar = (timestamp) => {
      if (!plotDivRef.current || telemetryData.length === 0) return;

      const elapsed = timestamp - animationStartTimeRef.current;
      const basePointsPerSecond = 5;
      const effectivePointsPerSecond = basePointsPerSecond * speedMultiplierValueRef.current;

      if (effectivePointsPerSecond <= 0) {
        animationFrameIdRef.current = requestAnimationFrame(animateCar);
        return;
      }

      const index = Math.floor(elapsed / (1000 / effectivePointsPerSecond));

      if (index >= telemetryData.length) {
        animationStartTimeRef.current = timestamp;
        currentPointIndexRef.current = 0;
      } else {
        currentPointIndexRef.current = index;
      }

      const point = telemetryData[currentPointIndexRef.current];
      if (point) {
        setCurrentPointTime(point.date);

        try {
          Plotly.restyle(plotDivRef.current, {
            x: [[point.x]],
            y: [[point.y]],
            text: [[`Driver: ${selectedDriver?.full_name || selectedDriver?.driver_number}<br>Time: ${new Date(point.date).toLocaleTimeString('pt-BR')}`]]
          }, [1]);
        } catch (err) {
          console.warn("Erro ao animar ponto:", err);
        }
      }

      animationFrameIdRef.current = requestAnimationFrame(animateCar);
    };

    if (animationFrameIdRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
    }
    animationFrameIdRef.current = requestAnimationFrame(animateCar);

    return () => {
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
        animationFrameIdRef.current = null;
      }
    };
  }, [telemetryData, selectedDriver]);

  // Handlers para os botões de controle de velocidade
  const handleSlower = () => {
    setSpeedMultiplier(prevSpeed => {
      const newSpeed = Math.max(0.1, prevSpeed * 0.5); // 50% mais lento, mínimo 0.1x
      speedMultiplierValueRef.current = newSpeed;
      return newSpeed;
    });
  };

  const handleFaster = () => {
    setSpeedMultiplier(prevSpeed => {
      const newSpeed = Math.min(10.0, prevSpeed * 1.5); // 50% mais rápido, máximo 10x
      speedMultiplierValueRef.current = newSpeed;
      return newSpeed;
    });
  };

  if (loading) return <p>Carregando mapa da pista...</p>;
  if (error) return <p style={{ color: 'red' }}>Erro: {error}</p>;
  if (!selectedDriver && allDrivers.length === 0) return <p>Nenhum driver encontrado para esta sessão.</p>;
  if (selectedDriver && telemetryData.length === 0) return <p>Nenhum dado de telemetria para o driver {selectedDriver.full_name || selectedDriver.driver_number}.</p>;

  const trackTrace = {
    x: telemetryData.map(d => d.x),
    y: telemetryData.map(d => d.y),
    mode: 'lines',
    name: 'Traçado da Pista',
    line: { color: 'gray', width: 1 },
    hoverinfo: 'none'
  };

  const initialPoint = telemetryData[0];
  const carPositionTrace = {
    x: [initialPoint?.x],
    y: [initialPoint?.y],
    mode: 'markers',
    marker: { size: 10, color: 'red' },
    name: 'Carro',
    hoverinfo: 'x+y+text',
    text: [`Driver: ${selectedDriver?.full_name || selectedDriver?.driver_number}<br>Time: ${new Date(initialPoint?.date).toLocaleTimeString('pt-BR')}`]
  };

  const layout = {
    autosize: true,
    margin: { l: 20, r: 20, b: 20, t: 20 },
    xaxis: { showgrid: false, zeroline: false, visible: false, scaleanchor: "y", scaleratio: 1 },
    yaxis: { showgrid: false, zeroline: false, visible: false, scaleanchor: "x", scaleratio: 1 },
    plot_bgcolor: 'transparent',
    paper_bgcolor: 'transparent',
    showlegend: false,
    hovermode: 'closest',
  };

  return (
    <div className="track-map-container">
      
      <Plot
        data={[trackTrace, carPositionTrace]}
        layout={layout}
        config={{ displayModeBar: false, responsive: true }}
        useResizeHandler={true}
        style={{ width: '100%', height: '100%' }}
        onInitialized={(figure, graphDiv) => { plotDivRef.current = graphDiv; }}
        onUpdate={(figure, graphDiv) => { plotDivRef.current = graphDiv; }}
      />
      <div className="telemetry-navigation-controls">
        <button className="telemetry-nav-button" onClick={handleSlower}>Slower</button>
        <span className="telemetry-current-time">
          {currentPointTime
            ? new Date(currentPointTime).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
            : currentTelemetryWindowStart?.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) || 'N/A'}
        </span>
        <button className="telemetry-nav-button" onClick={handleFaster}>Faster</button>
      </div>
    </div>
  );
}

export default TrackMap;
