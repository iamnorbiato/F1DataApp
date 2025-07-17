/* G:\Learning\F1Data\F1Data_Web\src\SessionResultsPanel.js V22 */

import React, { useState, useEffect } from 'react';

function SessionResultsPanel({ sessionKey }) {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Certifique-se de que esta URL base reflita a porta interna correta
  // (se for 30080 no seu ambiente local, ajuste)
  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://home:30008';

  useEffect(() => {
    const fetchSessionResults = async () => {
      if (!sessionKey) { // Se não houver sessionKey, não faz a requisição
        setResults([]);
        setLoading(false);
        return;
      }

      setLoading(true); // Começa a carregar
      setError(null);   // Limpa erros anteriores
      try {
        const response = await fetch(`${API_BASE_URL}/api/session-results-by-session/?session_key=${sessionKey}`);
        if (!response.ok) {
          throw new Error(`Erro ao buscar resultados da sessão: ${response.status} ${response.statusText}`);
        }
        const data = await response.json();
        // Os dados devem vir ordenados do backend como você especificou
        setResults(data || []);
      } catch (err) {
        console.error("Geni: Erro ao carregar resultados da sessão:", err);
        setError(err.message || 'Erro ao carregar resultados.');
      } finally {
        setLoading(false); // Termina o carregamento, com sucesso ou falha
      }
    };

    fetchSessionResults();
  }, [sessionKey, API_BASE_URL]); // O efeito roda novamente se sessionKey ou API_BASE_URL mudarem

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

  return (
    <div className="session-results-panel">
      <h2>Resultado da Sessão</h2>
        <div className="session-results-content">
          <ul className="session-results-list">
            {/* Aqui você pode ajustar quais campos do Serializer serão exibidos */}
            {results.map((item, index) => (
              <li key={item.driver_number || index} className="session-results-item">
                <span className="position">#{item.position || '-'}</span>
                <span className="pilot">Driver #{item.driver_number}</span>
                {item.time_gap && <span className="time-gap">Tempo: {item.time_gap}</span>}
                {item.number_of_laps && <span className="laps">Voltas: {item.number_of_laps}</span>}
              </li>
            ))}
          </ul>
      </div>
    </div>
  );
}

export default SessionResultsPanel;