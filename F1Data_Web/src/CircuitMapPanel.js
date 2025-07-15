// G:\Learning\F1Data\F1Data_Web\src\CircuitMapPanel.js
import React, { useState, useEffect } from 'react';
import Weather from './Weather';

function CircuitMapPanel({ circuitref, circuitShortName, selectedSessionKey }) {
  const [exists, setExists] = useState(true);

  const svgPath = `/Circuits/${circuitref}.svg`;

  useEffect(() => {
    // Testa a existência do SVG (somente em ambiente local/produção com permissão CORS)
    fetch(svgPath, { method: 'HEAD' })
      .then(res => {
        if (!res.ok) setExists(false);
        else setExists(true);
      })
      .catch(() => setExists(false));
  }, [svgPath]);

  if (!circuitref) return <p>Nenhum circuito selecionado</p>;
  if (!exists) return <p>SVG de {circuitref} não encontrado.</p>;
 
  return (
    <div className="circuit-map-panel">
      <h2>Traçado de {circuitShortName || 'Circuito'}</h2>
        <object type="image/svg+xml" data={svgPath} className="circuit-svg" aria-label={`Circuito ${circuitref}`}>
          Seu navegador não suporta SVG.
        </object>

    </div>
  );
}

export default CircuitMapPanel;
