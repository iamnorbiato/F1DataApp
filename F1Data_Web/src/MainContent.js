// G:\Learning\F1Data\F1Data_Web\src\MainContent.js
import React, { useState, useEffect } from 'react';
import Quadrant1Sessions from './Quadrant1Sessions';
import DriversList from './DriversList'; // Importa o novo componente DriversList

function MainContent({ meetingKey }) {
  // Estado para a chave da sessão selecionada
  const [selectedSessionKey, setSelectedSessionKey] = useState(null);
  // Estado para o nome da sessão selecionada (opcional, mas útil para exibição)
  const [selectedSessionName, setSelectedSessionName] = useState('');

  // Efeito para resetar a sessão selecionada quando o meetingKey mudar
  useEffect(() => {
    setSelectedSessionKey(null);
    setSelectedSessionName('');
  }, [meetingKey]);

  // Função que será passada para Quadrant1Sessions para lidar com a seleção
  const handleSessionSelect = (sessionKey, sessionName) => {
    setSelectedSessionKey(sessionKey);
    setSelectedSessionName(sessionName);
    console.log(`MainContent: Sessão selecionada - Key: ${sessionKey}, Nome: ${sessionName}`);
  };

  // Se não houver meetingKey, exibe a mensagem de placeholder
  if (!meetingKey) {
    return (
        <div className="welcome-message"> {/* Use a classe existente para a mensagem de boas-vindas */}
            <p>Nenhum evento selecionado. Por favor, selecione um no menu.</p>
        </div>
    );
  }

  return (
    // Removido o div extra. Agora retorna diretamente a grade principal de conteúdo.
    <div className="main-content-grid"> {/* Esta classe já está no seu App.css para o layout de grid */}
      {/* Quadrante 1: Lista de Sessões */}
      <div className="quadrant q1-sessions">
        <h3>Sessões</h3>
        <Quadrant1Sessions
            meetingKey={meetingKey}
            onSessionSelect={handleSessionSelect} // Passa a função de callback
            selectedSessionKey={selectedSessionKey} // Passa a sessão selecionada para destaque
        />
      </div>

      {/* Quadrante 2: Lista de Drivers (agora renderizado condicionalmente) */}
      <div className="quadrant q2-mock"> {/* O seu "Placeholder para 2ª Opção" */}
        <h3>Participantes da Sessão</h3>
        {selectedSessionKey ? (
          // Renderiza DriversList apenas se uma sessão foi selecionada
          <DriversList sessionKey={selectedSessionKey} />
        ) : (
          <p>Selecione uma sessão para ver os drivers.</p>
        )}
      </div>

      {/* Quadrante 3 e 4 permanecem como placeholders por enquanto */}
      <div className="quadrant q3-mock">
        <h3>Placeholder para 3ª Opção</h3>
        <p>Conteúdo da 3ª opção aqui.</p>
      </div>
      <div className="quadrant q4-details"> {/* Corrigido de q4-mock para q4-details conforme seu CSS */}
        <h3>Detalhes da Sessão / Grid de Largada</h3>
        <p>Este quadrante pode exibir mais detalhes da sessão ou o grid de largada.</p>
      </div>
    </div>
  );
}

export default MainContent;