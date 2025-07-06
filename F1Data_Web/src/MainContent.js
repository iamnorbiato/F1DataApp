// G:\Learning\F1Data\F1Data_Web\src\MainContent.js
import React from 'react';
import Quadrant1Sessions from './Quadrant1Sessions'; 

function MainContent({ meetingKey, meetingOfficialName }) {
  return (
    <div className="main-content-grid"> {/* Grid para os 4 quadrantes */}

      {/* Quadrante 1: Superior Esquerdo - Para Sessions */}
      <div className="quadrant quadrant-1">
        <div className="quadrant-content-box">
          <h3>Sessões do Evento: {meetingOfficialName} ({meetingKey})</h3>
          {/* Aqui o componente Quadrant1Sessions vai carregar e exibir os dados */}
          <Quadrant1Sessions meetingKey={meetingKey} />
        </div>
      </div>

      {/* Quadrante 2: Superior Direito (Placeholder) */}
      <div className="quadrant quadrant-2">
        <div className="quadrant-content-box">
          <h3>Métricas da Frota (Q2)</h3>
          <p>Conteúdo placeholder do Quadrante 2.</p>
        </div>
      </div>

      {/* Quadrante 3: Inferior Direito (Placeholder) */}
      <div className="quadrant quadrant-3">
        <div className="quadrant-content-box">
          <h3>Desempenho por Piloto (Q3)</h3>
          <p>Conteúdo placeholder do Quadrante 3.</p>
        </div>
      </div>

      {/* Quadrante 4: Inferior Esquerdo (Placeholder) */}
      <div className="quadrant quadrant-4">
        <div className="quadrant-content-box">
          <h3>Regras e Parâmetros (Q4)</h3>
          <p>Conteúdo placeholder do Quadrante 4.</p>
        </div>
      </div>

    </div>
  );
}

export default MainContent;