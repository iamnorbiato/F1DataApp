// G:\Learning\F1Data\F1Data_Web\src\App.js

import React, { useState, useEffect } from 'react';
import './App.css';
import MeetingsList from './MeetingsList';
import Sessions from './Sessions';
import CircuitMapPanel from './CircuitMapPanel';
import SessionResultsPanel from './SessionResultsPanel';
import RaceControl from './RaceControl'; // Importando o novo componente RaceControl
import { API_BASE_URL } from './api'; // ajuste o caminho se necessário
console.log('API_BASE_URL:', API_BASE_URL);

function App() {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [showYearDropdown, setShowYearDropdown] = useState(false);
  const [availableYears, setAvailableYears] = useState([]);
  const [selectedYear, setSelectedYear] = useState(null);
  const [selectedMeetingKey, setSelectedMeetingKey] = useState(null);
  const [selectedMeetingName, setSelectedMeetingName] = useState(null);
  const [selectedCircuitShortName, setSelectedCircuitShortName] = useState(null);
  const [selectedSessionKey, setSelectedSessionKey] = useState(null);
  const [circuitRef, setCircuitRef] = useState(null); // Este será o circuitref REAL do SVG

  const handleHamburgerClick = () => {
    setIsMobileMenuOpen(!isMobileMenuOpen);
  };

  const handleCloseMobileMenu = () => {
    setIsMobileMenuOpen(false);
    setShowYearDropdown(false);
    setTimeout(() => setShowYearDropdown(false), 50);
  };

  const handleRacesClick = async (e) => {
    e.preventDefault();
    if (!showYearDropdown && availableYears.length === 0) {
      try {
        const response = await fetch(`${API_BASE_URL}/api/filters/meetings/`);
        if (!response.ok) {
          throw new Error(`Erro ao buscar anos: ${response.status}`);
        }
        const data = await response.json();
        setAvailableYears(data.available_years || []);
      } catch (error) {
        console.error("Erro ao carregar anos:", error);
        setAvailableYears([]);
      }
    }
    setShowYearDropdown(prev => !prev);
  };

  const handleYearSelect = (year) => {
    setSelectedYear(year);
    setSelectedMeetingKey(null);
    setSelectedMeetingName(null);
    setSelectedCircuitShortName(null);
    setSelectedSessionKey(null); // Reseta a sessão selecionada ao mudar o ano
    setCircuitRef(null); // Reseta o circuitRef ao mudar o ano
    setShowYearDropdown(false);
    if (isMobileMenuOpen) setIsMobileMenuOpen(false);
  };

  // MODIFICAÇÃO PRINCIPAL AQUI: handleMeetingSelect agora é async e faz a busca do circuitRef
  const handleMeetingSelect = async (meetingKey, meetingName, circuitShortName, circuitKeyForCircuit) => {

    setSelectedMeetingKey(meetingKey);
    setSelectedMeetingName(meetingName);
    setSelectedCircuitShortName(circuitShortName);
    setSelectedSessionKey(null); // Reseta a sessão selecionada
    setCircuitRef(null); // Reseta o circuitRef enquanto a nova busca ocorre (para evitar exibir o SVG antigo)

    // FAÇA A CHAMADA DA API AQUI DENTRO!
    if (circuitKeyForCircuit) {
      try {
        const response = await fetch(`${API_BASE_URL}/api/circuit/?circuit_key=${circuitKeyForCircuit}`);

        if (!response.ok) {
          console.error(`ERROR: Erro ao buscar circuit ref para circuit_key ${circuitKeyForCircuit}: ${response.status} ${response.statusText}`);
          setCircuitRef(null);
          return;
        }
        const data = await response.json();

        if (data && data.circuitref) {
          const finalCircuitRef = data.circuitref.toLowerCase();
          setCircuitRef(finalCircuitRef); // Definir o circuitRef real e em minúsculas
        } else {
          console.warn(`WARN: Circuit ref não encontrado na resposta para circuit_key: ${circuitKeyForCircuit}`, data);
          setCircuitRef(null);
        }
      } catch (error) {
        console.error("ERROR: Geni: Erro ao buscar o circuit ref da API:", error);
        setCircuitRef(null);
      }
    } else {
      setCircuitRef(null); // Garante que circuitRef é null se não houver circuitKeyForCircuit
    }

    setShowYearDropdown(false);
    if (isMobileMenuOpen) setIsMobileMenuOpen(false);
  };

  const handleSessionSelect = (sessionKey, sessionName, dateStart) => {
    setSelectedSessionKey(sessionKey);
    // Aqui você também precisará buscar os dados meteorológicos para esta sessão (próxima etapa)
    console.log(`Sessão selecionada: ${sessionKey}, ${sessionName}, ${dateStart}`);
  };

  return (
    <div className="App">
      <header className="main-header">
        <a href="/" className="header-logo">tsalbouDTA</a>
        <nav className="header-nav">
          <div className="header-nav-item-wrapper">
            <a href="/corridas" className="header-nav-item" onClick={handleRacesClick}>Corridas</a>
            {showYearDropdown && (
              <div className="year-dropdown desktop-dropdown">
                {availableYears.length > 0 ? (
                  availableYears.map(year => (
                    <div key={year} className="year-dropdown-item" onClick={() => handleYearSelect(year)}>{year}</div>
                  ))
                ) : (
                  <p>Carregando anos...</p>
                )}
              </div>
            )}
          </div>
          <a href="/equipes" className="header-nav-item">Equipes</a>
          <a href="/pilotos" className="header-nav-item">Pilotos</a>
          <a href="/circuitos" className="header-nav-item">Circuitos</a>
          <a href="/telemetria" className="header-nav-item">Telemetria</a>
          <a href="/livetimming" className="header-nav-item">LiveTimming</a>
        </nav>
        <button className="hamburger-menu-button" onClick={handleHamburgerClick}>&#9776;</button>
      </header>

      <main className="main-content-area">
        {selectedYear && (
          <div className="main-grid-layout">
            <div className="left-panel-group-horizontal">
              <MeetingsList
                selectedYear={selectedYear}
                onMeetingSelect={handleMeetingSelect}
                selectedMeetingKey={selectedMeetingKey}
              />
              {selectedMeetingKey && (
                <div className="sessions-side-panel">
                  <h2>Sessões de {selectedCircuitShortName || selectedMeetingName || 'Corrida Selecionada'}</h2>
                  <Sessions
                    meetingKey={selectedMeetingKey}
                    onSessionSelect={handleSessionSelect}
                    selectedSessionKey={selectedSessionKey}
                  />
                </div>
              )}
            </div>

            {selectedSessionKey && circuitRef && (
              <CircuitMapPanel
                circuitref={circuitRef}
                selectedSessionKey={selectedSessionKey}
                circuitShortName={selectedCircuitShortName}
              />
            )}

            {selectedSessionKey && (
              <SessionResultsPanel sessionKey={selectedSessionKey} />
            )}

            {selectedSessionKey && (
              <RaceControl sessionKey={selectedSessionKey} />
            )}
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
