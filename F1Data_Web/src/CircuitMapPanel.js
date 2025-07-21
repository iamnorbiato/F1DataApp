// G:\Learning\F1Data\F1Data_Web\src\CircuitMapPanel.js
import React, { useState } from 'react';
import { API_BASE_URL } from './api'; // ajuste o caminho se necessário
console.log('API_BASE_URL:', API_BASE_URL);


function CircuitMapPanel({ circuitref, circuitShortName, selectedSessionKey }) {
  const [imageError, setImageError] = useState(false);

  const lowerCaseCircuitRef = circuitref ? circuitref.toLowerCase() : '';
  const svgPath = `/Circuits/${lowerCaseCircuitRef}.svg`;
  const pngPath = `/Circuits/${lowerCaseCircuitRef}.png`;

  // --- Renderização Condicional ---
  if (!circuitref) {
    return (
      <div className="circuit-map-panel">
        <h2>Traçado de Circuito</h2>
        <p>Nenhum circuito selecionado.</p>
      </div>
    );
  }

  const imageToUse = imageError ? pngPath : svgPath;

  return (
    <div className="circuit-map-panel">
      <h2>Traçado de {circuitShortName || 'Circuito'}</h2>
      <div className="circuit-map-content">
        <img
          src={imageToUse}
          alt={`Circuito ${circuitref}`}
          onError={() => setImageError(true)}
          className="circuit-svg"
        />
      </div>
    </div>
  );
}

export default CircuitMapPanel;
