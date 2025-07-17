import React from 'react';

function SessionResultsPanel({ sessionKey }) {
  const mockResults = [
    { position: 1, pilot: 'Lewis Hamilton' },
    { position: 2, pilot: 'Max Verstappen' },
    { position: 3, pilot: 'Charles Leclerc' },
    { position: 4, pilot: 'Lando Norris' },
    { position: 5, pilot: 'Fernando Alonso' },
  ];

  return (
    <div className="session-results-panel">
      <h2>Resultado da Sessão</h2>
      <ul className="session-results-list">
        {mockResults.map((item, index) => (
          <li key={index} className="session-results-item">
            <span className="position">#{item.position}</span>
            <span className="pilot">{item.pilot}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default SessionResultsPanel;
