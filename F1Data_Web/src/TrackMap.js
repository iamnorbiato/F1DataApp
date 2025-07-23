// G:\Learning\F1Data\F1Data_Web\src\TrackMap.js
import React, { useState, useEffect, useRef } from 'react';
import Plotly from 'plotly.js/dist/plotly-basic';
import Plot from 'react-plotly.js';
import { API_BASE_URL } from './api';

const formatTimeWithMilliseconds = (dateString) => {
  if (!dateString) return 'N/A';
  const date = new Date(dateString);
  const hours = String(date.getUTCHours()).padStart(2, '0');
  const minutes = String(date.getUTCMinutes()).padStart(2, '0');
  const seconds = String(date.getUTCSeconds()).padStart(2, '0');
  const milliseconds = String(date.getUTCMilliseconds()).padStart(3, '0').substring(0, 2);
  return `${hours}:${minutes}:${seconds}.${milliseconds}`;
};

const formatDateUTCISOString = (date) => date.toISOString();

function TrackMap({ sessionKey, startDate, endDate, selectedDriver }) {
  const [telemetryData, setTelemetryData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentTelemetryWindowStart, setCurrentTelemetryWindowStart] = useState(null);
  const [currentPointTime, setCurrentPointTime] = useState(null);
  const [speedMultiplier, setSpeedMultiplier] = useState(1.0);

  const speedMultiplierValueRef = useRef(1.0);
  const animationFrameIdRef = useRef(null);
  const currentPointIndexRef = useRef(0);
  const plotDivRef = useRef(null);
  const animationStartTimeRef = useRef(performance.now());

  const sessionStartDateRef = useRef(null);
  const sessionEndDateRef = useRef(null);
  const initialWindowStartRef = useRef(null);

  useEffect(() => {
    if (sessionKey && startDate) {
      const start = new Date(startDate);
      if (!isNaN(start)) {
        initialWindowStartRef.current = start;
        setCurrentTelemetryWindowStart(start);
        sessionStartDateRef.current = start;
      }
    } else {
      initialWindowStartRef.current = null;
      setCurrentTelemetryWindowStart(null);
      sessionStartDateRef.current = null;
    }
  }, [sessionKey, startDate]);

  useEffect(() => {
    if (endDate) {
      const end = new Date(endDate);
      sessionEndDateRef.current = isNaN(end) ? null : end;
    } else {
      sessionEndDateRef.current = null;
    }
  }, [endDate]);

  useEffect(() => {
    if (!selectedDriver || !sessionKey || !currentTelemetryWindowStart) {
      setTelemetryData([]);
      setLoading(false);
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
        animationFrameIdRef.current = null;
      }
      return;
    }

    const fetchTelemetryData = async () => {
      setLoading(true);
      setError(null);
      try {
        const url = `${API_BASE_URL}/api/location-by-session-and-driver/?session_key=${sessionKey}&driver_number=${selectedDriver.driver_number}&date=${formatDateUTCISOString(currentTelemetryWindowStart)}`;
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

    fetchTelemetryData();
  }, [sessionKey, selectedDriver, currentTelemetryWindowStart]);

  useEffect(() => {
    if (telemetryData.length < 2 || !plotDivRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
      return;
    }

    currentPointIndexRef.current = 0;
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
        const WINDOW_MINUTES = 10;
        if (currentTelemetryWindowStart && sessionEndDateRef.current) {
          const newStart = new Date(currentTelemetryWindowStart.getTime() + WINDOW_MINUTES * 60 * 1000);
          if (newStart < sessionEndDateRef.current) {
            setCurrentTelemetryWindowStart(newStart);
          }
        }
        return;
      }

      const point = telemetryData[currentPointIndexRef.current];
      if (point) {
        setCurrentPointTime(point.date);
        try {
          Plotly.restyle(plotDivRef.current, {
            x: [[point.x]],
            y: [[point.y]],
            text: [[`Driver: ${selectedDriver?.full_name || selectedDriver?.driver_number}<br>Time: ${formatTimeWithMilliseconds(point.date)}`]]
          }, [1]);
        } catch (err) {
          console.warn("Erro ao animar ponto:", err);
        }
      }

      currentPointIndexRef.current++;
      animationFrameIdRef.current = requestAnimationFrame(animateCar);
    };

    cancelAnimationFrame(animationFrameIdRef.current);
    animationFrameIdRef.current = requestAnimationFrame(animateCar);

    return () => cancelAnimationFrame(animationFrameIdRef.current);
  }, [telemetryData, selectedDriver]);

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

  if (loading) return <p>Carregando mapa da pista...</p>;
  if (!selectedDriver) return <p>Nenhum piloto selecionado para o mapa da pista.</p>;
  if (error) return <p style={{ color: 'red' }}>Erro: {error}</p>;
  if (selectedDriver && telemetryData.length === 0)
    return <p>Nenhum dado de telemetria para o driver {selectedDriver.full_name || selectedDriver.driver_number} nesta janela de tempo.</p>;

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
    x: initialPoint ? [initialPoint.x] : [],
    y: initialPoint ? [initialPoint.y] : [],
    mode: 'markers',
    marker: { size: 10, color: 'red' },
    name: 'Carro',
    hoverinfo: 'x+y+text',
    text: initialPoint
      ? [`Driver: ${selectedDriver?.full_name || selectedDriver?.driver_number}<br>Time: ${formatTimeWithMilliseconds(initialPoint.date)}`]
      : []
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
    <div className="track-map-container drivers-list-panel">
      <h2 className="panel-title">Movimentação na Pista: {selectedDriver?.full_name || `Driver ${selectedDriver?.driver_number}`}</h2>

      <Plot
        data={[trackTrace, carPositionTrace]}
        layout={layout}
        config={{ displayModeBar: false, responsive: true }}
        useResizeHandler={true}
        style={{ width: '100%', height: '100%' }}
        onInitialized={(figure, graphDiv) => { plotDivRef.current = graphDiv; }}
        onUpdate={(figure, graphDiv) => { plotDivRef.current = graphDiv; }}
      />

      <div className="track-map-controls">
        <button className="track-map-nav-button" onClick={handleSlower}>&lt;&lt;&lt;</button>
        <span className="track-map-current-time">
          {currentPointTime
            ? formatTimeWithMilliseconds(currentPointTime)
            : (currentTelemetryWindowStart ? formatTimeWithMilliseconds(currentTelemetryWindowStart) : 'N/A')}
        </span>
        <button className="track-map-nav-button" onClick={handleFaster}>&gt;&gt;&gt;</button>
      </div>
    </div>
  );
}

export default TrackMap;
