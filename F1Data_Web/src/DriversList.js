// G:\Learning\F1Data\F1Data_Web\src\DriversList.js
import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from './api'; // ajuste o caminho se necessário
console.log('API_BASE_URL:', API_BASE_URL);

// MODIFICADO: Agora recebe a prop onDriverSelect (callback do pai)
function DriversList({ sessionKey, onDriverSelect }) {
  const [drivers, setDrivers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchDrivers = async () => {
      if (!sessionKey) {
        setDrivers([]);
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const driversApiUrl = `${API_BASE_URL}/api/drivers-by-session/?session_key=${sessionKey}`;
        const response = await fetch(driversApiUrl);
        if (!response.ok) {
          throw new Error(`Erro ao buscar drivers: ${response.status} ${response.statusText}`);
        }
        const data = await response.json();
        setDrivers(data);
      } catch (err) {
        console.error('Erro em DriversList ao buscar drivers:', err);
        setError(err.message || 'Erro ao carregar drivers.');
      } finally {
        setLoading(false);
      }
    };
    fetchDrivers();
  }, [sessionKey, API_BASE_URL]);

  // Handler para o clique em um item de driver
  const handleDriverClick = (driver) => {
    // Chama a função de callback passada via prop, enviando o driver completo
    if (onDriverSelect) {
      onDriverSelect(driver); 
    }
  };

  if (loading) {
    return <p>Carregando drivers...</p>;
  }

  if (error) {
    return <p style={{ color: 'red' }}>Erro: {error}</p>;
  }

  if (drivers.length === 0) {
    return <p>Nenhum driver encontrado para esta sessão.</p>;
  }

  return (
    <div className="drivers-list-container">
      <div className="drivers-table-header">
        <span>Número</span>
        <span>Driver</span>
        <span>Equipe</span>
      </div>
      <div className="drivers-list-scrollable">
        {drivers.map(driver => (
          <div
            key={driver.driver_number}
            className="driver-list-item"
            onClick={() => handleDriverClick(driver)} // NOVO: Torna o item clicável
            style={{ cursor: 'pointer' }} // Adiciona um cursor de ponteiro para indicar clicabilidade
          >
            <span>{driver.driver_number}</span>
            <span>{driver.full_name} ({driver.country_code})</span>
            <span style={{ color: `#${driver.team_colour}` }}>{driver.team_name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default DriversList;