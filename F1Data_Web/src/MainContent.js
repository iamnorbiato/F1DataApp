// G:\Learning\F1Data\F1Data_App\core\MainContent.js
import React from 'react';
import Quadrant1Sessions from './Quadrant1Sessions'; 

function MainContent({ meetingKey, meetingOfficialName }) {
  return (
    <div className="main-content-grid"> {/* Grid para os 4 quadrantes */}
      
      {/* Quadrante 1: Superior Esquerdo - Para Sessions */}
      <div className="quadrant q1-sessions">
        <div className="quadrant-content-box">
          <h3>{meetingOfficialName}</h3> 
          <Quadrant1Sessions meetingKey={meetingKey} />
        </div>
      </div>

      {/* Quadrante 2: Superior Central (Placeholder) */}
      <div className="quadrant q2-mock">
        <div className="quadrant-content-box">
          <h3>Placeholder para 2ª Opção</h3>
          <p>Conteúdo mockado do Quadrante Central Superior.</p>
        </div>
      </div>

      {/* Quadrante 3: Superior Direito (Placeholder) */}
      <div className="quadrant q3-mock">
        <div className="quadrant-content-box">
          <h3>Placeholder para 3ª Opção</h3>
          <p>Conteúdo mockado do Quadrante Superior Direito.</p>
        </div>
      </div>

      {/* Quadrante 4: Inferior (Placeholder) */}
      <div className="quadrant q4-details">
        <div className="quadrant-content-box">
          <h3>Regras e Parâmetros (Q4)</h3>
          <p>Conteúdo mockado do Quadrante Inferior (detalhes gerais).</p>
        </div>
      </div>

    </div>
  );
}

export default MainContent;