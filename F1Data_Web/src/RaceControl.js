// src/F1Data_Web/src/RaceControl.js

import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from './api'; 
console.log('API_BASE_URL (RaceControl.js):', API_BASE_URL);

// Função auxiliar para formatar a session_date (YYYY-MM-DD HH:MM:SS)
const formatSessionDate = (dateString) => {
  if (!dateString) return 'N/A';
  return dateString; 
};

function RaceControl({ sessionKey }) {
  const [raceControlData, setRaceControlData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchRaceControlData = async () => {
      if (!sessionKey) {
        setRaceControlData([]);
        setLoading(false);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const response = await fetch(`${API_BASE_URL}/api/race-control-by-session/?session_key=${sessionKey}`);
        if (!response.ok) {
          throw new Error(`Erro ao buscar dados de Race Control: ${response.status} ${response.statusText}`);
        }
        const data = await response.json();
        
        // --- LOGS DE DEBUG AQUI ---
        console.log('Dados recebidos da API RaceControl:', data);
        console.log('Número de itens RaceControl:', data.length);
        // --- FIM DOS LOGS DE DEBUG ---

        setRaceControlData(data || []);
      } catch (err) {
        console.error("Geni: Erro ao carregar dados de Race Control:", err);
        setError(err.message || 'Erro ao carregar dados de Race Control.');
      } finally {
        setLoading(false);
      }
    };

    fetchRaceControlData();
  }, [sessionKey, API_BASE_URL]);

  // --- LOGS DE DEBUG NA RENDERIZAÇÃO ---
  console.log('Estado de carregamento RaceControl:', loading);
  console.log('Dados RaceControl no estado:', raceControlData);
  // --- FIM DOS LOGS DE DEBUG ---

  if (loading) {
    return (
      <div className="race-control-panel">
        <h2>Controle de Corrida</h2>
        <p>Carregando eventos de controle de corrida...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="race-control-panel">
        <h2>Controle de Corrida</h2>
        <p style={{ color: 'red' }}>Erro: {error}</p>
      </div>
    );
  }

  if (raceControlData.length === 0) {
    return (
      <div className="race-control-panel">
        <h2>Controle de Corrida</h2>
        <p>Nenhum evento de controle de corrida encontrado para esta sessão.</p>
      </div>
    );
  }

  return (
    <div className="race-control-panel">
      <h2>Controle de Corrida</h2>
      {/* Cabeçalho da tabela de RaceControl */}
      <div className="race-control-table-header">
        <span className="header-rc-date">Date</span>
        <span className="header-rc-driver">Driver</span>
        <span className="header-rc-lap">Lap</span>
        <span className="header-rc-category">Category</span>
        <span className="header-rc-flag">Flag</span>
        <span className="header-rc-scope">Scope</span>
        <span className="header-rc-sector">Sector</span>
        <span className="header-rc-message">Message</span>
      </div>

      {/* Lista de eventos de RaceControl */}
      <div className="race-control-content"> 
        <ul className="race-control-list">
          {raceControlData.map((item, index) => (
            <li key={index} className="race-control-item"> 
              <span className="rc-date">{formatSessionDate(item.session_date)}</span>
              <span className="rc-driver">
                {item.driver_number ? `${item.driver_number} ` : ''}
                {item.broadcast_name || '-'}
                {/* CORREÇÃO AQUI: Verifica se team_name existe E não é uma string vazia */}
                {item.team_name && item.team_name.trim() !== '' ? ` (${item.team_name})` : ''} 
              </span> 
              <span className="rc-lap">{item.lap_number || '-'}</span>
              <span className="rc-category">{item.category || '-'}</span>
              <span className="rc-flag">{item.flag || '-'}</span>
              <span className="rc-scope">{item.scope || '-'}</span>
              <span className="rc-sector">{item.sector || '-'}</span>
              <span className="rc-message">{item.message || '-'}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default RaceControl;