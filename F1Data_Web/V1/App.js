// G:\Learning\F1Data\F1Data_Web\src\App.js
import React, { useState } from 'react';
import './App.css'; 

import Sidebar from './Sidebar';
import OptionChooserFrame from './OptionChooserFrame';
import MainContent from './MainContent'; // <--- NOVO: Importa o componente MainContent

function App() {
  const [activeMenuItem, setActiveMenuItem] = useState(null);
  const [selectedMeetingData, setSelectedMeetingData] = useState(null); // <--- NOVO: Estado para os dados do meeting selecionado

  const handleMenuItemClick = (menuItemName, menuItemLabel) => {
    // Se clicar no mesmo item, esconde o frame. Senão, mostra o novo.
    setActiveMenuItem(activeItem => 
      activeItem && activeItem.name === menuItemName ? null : { name: menuItemName, label: menuItemLabel }
    );
    setSelectedMeetingData(null); // <--- NOVO: Reseta os dados do meeting ao mudar de menu
  };

  const handleCloseFrame = () => {
    setActiveMenuItem(null); 
  };

  // <--- NOVO: Função para lidar com a busca de dados no OptionChooserFrame ---
  const handleSearchData = (meetingKey, meetingOfficialName) => {
    setSelectedMeetingData({
      meeting_key: meetingKey,
      meeting_official_name: meetingOfficialName
    });
    handleCloseFrame(); // Fecha o frame de opções após a busca
  };
  // ------------------------------------------------------------------------

  return (
    <div className="App">
      {/* Sidebar - Passa a função de clique */}
      <Sidebar
        onMenuItemClick={handleMenuItemClick}
        activeMenuItem={activeMenuItem ? activeMenuItem.name : null}
      />

      {/* Área de Conteúdo Principal (agora renderiza MainContent) */}
      <div className="main-content-container"> {/* <--- DIV ALTERADA PARA CONTAINER */}
        {selectedMeetingData ? (
          <MainContent 
            meetingKey={selectedMeetingData.meeting_key} 
            meetingOfficialName={selectedMeetingData.meeting_official_name} 
          />
        ) : (
          <div className="welcome-message">
            <h1>Dashboard F1Data</h1>
            <p>Selecione um item no menu lateral para começar a explorar os dados.</p>
          </div>
        )}
      </div>

      {/* Frame de opções dinâmico */}
      {activeMenuItem && (
        <OptionChooserFrame
          menuItemName={activeMenuItem.name}
          menuItemLabel={activeMenuItem.label}
          onClose={handleCloseFrame}
          onSearchData={handleSearchData} 
        />
      )}
    </div>
  );
}

export default App;