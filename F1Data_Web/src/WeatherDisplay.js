// G:\Learning\F1Data\F1Data_Web\src\WeatherDisplay.js
import React, { useState, useEffect } from 'react';
import WindRoseChart from './WindRoseChart'; // ajuste o caminho se necessário

function WeatherDisplay({ sessionKey }) {
  const [weatherData, setWeatherData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:30080';

  useEffect(() => {
    if (sessionKey) {
      const fetchWeatherData = async () => {
        setLoading(true);
        setError(null);
        try {
          const apiUrl = `${API_BASE_URL}/api/weather-by-session/?session_key=${sessionKey}`;
          console.log('DEBUG WeatherDisplay: URL da API de clima:', apiUrl);

          const response = await fetch(apiUrl);

          if (!response.ok) {
            throw new Error(`Erro HTTP: ${response.status} ${response.statusText}`);
          }

          const data = await response.json();
          console.log('DEBUG WeatherDisplay: Dados de clima recebidos:', data);

          // MUDANÇA AQUI: Usa a forma funcional de setWeatherData para comparar com o estado anterior
          setWeatherData(prevWeatherData => {
              if (JSON.stringify(data) !== JSON.stringify(prevWeatherData)) {
                  console.log('DEBUG WeatherDisplay: Dados de clima são diferentes, atualizando estado.');
                  return data; // Atualiza o estado
              } else {
                  console.log('DEBUG WeatherDisplay: Dados de clima são os mesmos, NÃO atualizando estado.');
                  return prevWeatherData; // Mantém o estado atual, evita nova render/ciclo
              }
          });

        } catch (err) {
          console.error('Erro ao buscar dados de clima:', err);
          setError(err.message || 'Erro ao carregar dados de clima.');
        } finally {
          setLoading(false); // Garante que o loading seja desativado em qualquer caso
        }
      };
      fetchWeatherData();
    } else {
      setWeatherData([]);
      setLoading(false);
    }
  }, [sessionKey, API_BASE_URL]); // MUDANÇA AQUI: REMOVIDO weatherData das dependências!

  if (loading) {
    return <p>Carregando dados de clima...</p>;
  }

  if (error) {
    return <p style={{ color: 'red' }}>Erro: {error}</p>;
  }

  if (weatherData.length === 0) {
    return <p>Nenhum dado de clima encontrado para esta sessão.</p>;
  }

  return (
    <div className="weather-display-container">
      {/* 🌪️ Rosa dos Ventos */}
      <div style={{ margin: '20px auto', maxWidth: 500 }}>
        <h3>Rosa dos Ventos (animação)</h3>
        <WindRoseChart weatherData={weatherData} />
      </div>
    </div>
  );
}

export default WeatherDisplay;