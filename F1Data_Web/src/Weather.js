// G:\Learning\F1Data\F1Data_Web\src\Weather.js

import React, { useState, useEffect } from 'react';

function Weather({ selectedSessionKey }) {
  const [weatherData, setWeatherData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:30080';

  useEffect(() => {
    const fetchWeatherData = async () => {
      if (!selectedSessionKey) {
        setWeatherData(null);
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        console.log(`Buscando dados meteorológicos para a sessão ${selectedSessionKey}...`);
        const response = await fetch(`${API_BASE_URL}/api/weather-by-session/?session_key=${selectedSessionKey}`);

        if (!response.ok) {
          throw new Error(`Erro ao buscar dados meteorológicos: ${response.status}`);
        }
        const data = await response.json();
        setWeatherData(data); // data é um ARRAY
        console.log("Dados meteorológicos recebidos:", data);
      } catch (err) {
        console.error("Geni: Erro ao carregar dados meteorológicos:", err);
        setError(err.message || 'Erro ao carregar dados meteorológicos.');
      } finally {
        setLoading(false);
      }
    };

    fetchWeatherData();
  }, [selectedSessionKey, API_BASE_URL]);

  // NOVO: Selecione o item de clima que você quer exibir
  // Por exemplo, o primeiro item do array (o mais antigo)
  const currentWeatherData = weatherData && weatherData.length > 0 ? weatherData[0] : null;

  return (
    <>
      {loading && <p>Carregando dados meteorológicos...</p>}
      {error && <p style={{ color: 'red' }}>Erro: {error}</p>}

      {/* Agora, use currentWeatherData para acessar as propriedades */}
      {currentWeatherData && ( // Renderiza se currentWeatherData não for nulo
        <div className="weather-overlays">
          {/* Ícones de chuva (se rainfall for true) */}
          {currentWeatherData.rainfall !== undefined && currentWeatherData.rainfall > 0 && ( // Verifique se rainfall é > 0
            <div className="weather-icon rain-icon" title="Chuva">🌧️</div>
          )}

          {/* Temperatura do ar/pista sobre o traçado */}
          {currentWeatherData.air_temperature !== undefined && (
            <div className="weather-temp air-temp" title="Temperatura do Ar">
              🌡️ {currentWeatherData.air_temperature}°C
            </div>
          )}
          {currentWeatherData.track_temperature !== undefined && (
            <div className="weather-temp track-temp" title="Temperatura da Pista">
              🔥 {currentWeatherData.track_temperature}°C
            </div>
          )}

          {/* Vetores de vento (direção + intensidade) como setas no mapa */}
          {currentWeatherData.wind_direction !== undefined && currentWeatherData.wind_speed !== undefined && (
            <div
              className="weather-wind"
              title={`Vento: ${currentWeatherData.wind_speed} m/s, Direção: ${currentWeatherData.wind_direction}°`}
              // Adicionando um estilo inline para rotação do vento
              style={{ transform: `rotate(${currentWeatherData.wind_direction}deg)` }}
            >
              💨
            </div>
          )}
        </div>
      )}
    </>
  );
}

export default Weather;