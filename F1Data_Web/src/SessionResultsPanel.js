import React from 'react';

function SessionResultsPanel({ sessionKey }) {
  const mockResults = [
    { position: 1, pilot: 'Lewis Hamilton' },
    { position: 2, pilot: 'Max Verstappen' },
    { position: 3, pilot: 'Charles Leclerc' },
    { position: 4, pilot: 'Lando Norris' },
    { position: 5, pilot: 'Fernando Alonso' },
    { position: 6, pilot: 'George Russell' },
    { position: 7, pilot: 'Carlos Sainz' },
    { position: 8, pilot: 'Sergio Pérez' },
    { position: 9, pilot: 'Oscar Piastri' },
    { position: 10, pilot: 'Yuki Tsunoda' },
    { position: 11, pilot: 'Pierre Gasly' },
    { position: 12, pilot: 'Esteban Ocon' },
    { position: 13, pilot: 'Alexander Albon' },
    { position: 14, pilot: 'Nico Hülkenberg' },
    { position: 15, pilot: 'Valtteri Bottas' },
    { position: 16, pilot: 'Kevin Magnussen' },
    { position: 17, pilot: 'Logan Sargeant' },
    { position: 18, pilot: 'Zhou Guanyu' },
    { position: 19, pilot: 'Daniel Ricciardo' },
    { position: 20, pilot: 'Liam Lawson' },
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
