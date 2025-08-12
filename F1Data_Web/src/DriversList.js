// G:\Learning\F1Data\F1Data_Web\src\DriversList.js
import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from './api';
import { useDriverHoverCard } from './utils/Functions'; // Importa o Hook
import './App.css';

console.log('API_BASE_URL:', API_BASE_URL);

function DriversList({ sessionKey, onDriverSelect, selectedDriverNumber }) {
  const [drivers, setDrivers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // INÍCIO DA CORREÇÃO: Usa o Hook customizado
  const { hoveredHeadshotUrl, mouseX, mouseY, handleMouseEnter, handleMouseLeave, handleMouseMove } = useDriverHoverCard();
  // FIM DA CORREÇÃO

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
  }, [sessionKey]);

  const handleDriverClick = (driver) => {
    if (onDriverSelect) {
      onDriverSelect(driver);
    }
  };

  if (loading) {
    return (
      <div>
        <h2>Pilotos da Sessão</h2>
        <p>Carregando drivers...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h2>Pilotos da Sessão</h2>
        <p style={{ color: 'red' }}>Erro: {error}</p>
      </div>
    );
  }

  if (drivers.length === 0) {
    return (
      <div>
        <h2>Pilotos da Sessão</h2>
        <p>Nenhum driver encontrado para esta sessão.</p>
      </div>
    );
  }

  return (
    <div
      className="drivers-list-panel"
      onMouseMove={handleMouseMove}
    >
      <h2>Pilotos da Sessão</h2>
      <div className="drivers-table-header">
        <span className="header-driver-num">Num</span>
        <span className="header-driver-pilot">Piloto</span>
        <span className="header-driver-team">Equipe</span>
      </div>
      <div className="drivers-list-content">
        <ul className="drivers-list-items">
          {drivers.map(driver => (
            <li
              key={driver.driver_number}
              className={`driver-list-item ${selectedDriverNumber === driver.driver_number ? 'active' : ''}`}
              onClick={() => handleDriverClick(driver)}
              onMouseEnter={() => handleMouseEnter(driver.headshot_url)}
              onMouseLeave={handleMouseLeave}
            >
              <span className="driver-num">{driver.driver_number}</span>
              <span className="driver-pilot">{driver.name_acronym} - {driver.full_name}</span>
              <span className="driver-team" style={{ color: `#${driver.team_colour}` }}>{driver.team_name}</span>
            </li>
          ))}
        </ul>
      </div>
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
  );
}

export default DriversList;