// G:\Learning\F1Data\F1Data_Web\src\App.js

import React, { useState, useEffect } from 'react';
import './App.css';
import MeetingsList from './MeetingsList';
import Sessions from './Sessions';
import CircuitMapPanel from './CircuitMapPanel';
import SessionResultsPanel from './SessionResultsPanel';
import RaceControl from './RaceControl';
import DriversList from './DriversList';
// NOVO: Importar TrackMap
import TrackMap from './TrackMap';
import { API_BASE_URL } from './api';
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
  const [circuitRef, setCircuitRef] = useState(null);
  const [menuOrigin, setMenuOrigin] = useState(null);
  const [selectedDriverNumber, setSelectedDriverNumber] = useState(null);
  // NOVOS ESTADOS: Para armazenar as datas da sessão
  const [selectedSessionStartDate, setSelectedSessionStartDate] = useState(null);
  const [selectedSessionEndDate, setSelectedSessionEndDate] = useState(null);
  // FIM NOVOS ESTADOS

  const handleHamburgerClick = () => {
    setIsMobileMenuOpen(!isMobileMenuOpen);
  };

  const handleCloseMobileMenu = () => {
    setIsMobileMenuOpen(false);
    setShowYearDropdown(false);
    setTimeout(() => setShowYearDropdown(false), 50);
  };

  const handleMenuClick = async (e, origin) => {
    e.preventDefault();
    if (menuOrigin === origin && showYearDropdown) {
      setShowYearDropdown(false);
      return;
    }
    
    setMenuOrigin(origin);
    
    setSelectedYear(null);
    setSelectedMeetingKey(null);
    setSelectedMeetingName(null);
    setSelectedCircuitShortName(null);
    setSelectedSessionKey(null);
    setSelectedSessionStartDate(null); // Resetar
    setSelectedSessionEndDate(null);   // Resetar
    setCircuitRef(null);
    setSelectedDriverNumber(null);

    if (availableYears.length === 0) {
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
    setShowYearDropdown(true);
  };

  const handleYearSelect = (year) => {
    setSelectedYear(year);
    setSelectedMeetingKey(null);
    setSelectedMeetingName(null);
    setSelectedCircuitShortName(null);
    setSelectedSessionKey(null);
    setSelectedSessionStartDate(null); // Resetar
    setSelectedSessionEndDate(null);   // Resetar
    setCircuitRef(null);
    setSelectedDriverNumber(null);
    setShowYearDropdown(false);
    if (isMobileMenuOpen) setIsMobileMenuOpen(false);
  };

  const handleMeetingSelect = async (meetingKey, meetingName, circuitShortName, circuitKeyForCircuit) => {
    setSelectedMeetingKey(meetingKey);
    setSelectedMeetingName(meetingName);
    setSelectedCircuitShortName(circuitShortName);
    setSelectedSessionKey(null);
    setSelectedSessionStartDate(null); // Resetar
    setSelectedSessionEndDate(null);   // Resetar
    setCircuitRef(null);
    setSelectedDriverNumber(null);

    if (menuOrigin === 'races' && circuitKeyForCircuit) {
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
          setCircuitRef(finalCircuitRef);
        } else {
          console.warn(`WARN: Circuit ref não encontrado na resposta para circuit_key: ${circuitKeyForCircuit}`, data);
          setCircuitRef(null);
        }
      } catch (error) {
        console.error("ERROR: Geni: Erro ao buscar o circuit ref da API:", error);
        setCircuitRef(null);
      }
    } else {
      setCircuitRef(null);
    }
    setShowYearDropdown(false);
    if (isMobileMenuOpen) setIsMobileMenuOpen(false);
  };

  // MODIFICADO: handleSessionSelect agora recebe dateEnd também
  const handleSessionSelect = (sessionKey, sessionName, dateStart, dateEnd) => {
    setSelectedSessionKey(sessionKey);
    setSelectedSessionStartDate(dateStart); // Salva a data de início
    setSelectedSessionEndDate(dateEnd);     // Salva a data de fim
    setSelectedDriverNumber(null); // Reseta o driver selecionado ao mudar a sessão
    console.log(`Sessão selecionada: ${sessionKey}, ${sessionName}, ${dateStart} (Origem: ${menuOrigin})`);
  };

  const handleDriverSelect = (driverNumber) => {
    setSelectedDriverNumber(driverNumber);
    console.log(`Driver selecionado: ${driverNumber}`);
  };

  return (
    <div className="App">
      <header className="main-header">
        <a href="/" className="header-logo">tsalbouDTA</a>
        <nav className="header-nav">
          <div className="header-nav-item-wrapper">
            <a href="/corridas" className="header-nav-item" onClick={(e) => handleMenuClick(e, 'races')}>Corridas</a>
            {menuOrigin === 'races' && showYearDropdown && (
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
          <div className="header-nav-item-wrapper">
            <a href="/telemetria" className="header-nav-item" onClick={(e) => handleMenuClick(e, 'telemetry')}>Telemetria</a>
            {menuOrigin === 'telemetry' && showYearDropdown && (
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
          <a href="/livetimming" className="header-nav-item">LiveTimming</a>
        </nav>
        <button className="hamburger-menu-button" onClick={handleHamburgerClick}>&#9776;</button>
      </header>

      <main className="main-content-area">
        {selectedYear && (
          <div className="main-grid-layout">
            {/* Primeira linha do Grid: MeetingsList, Sessions, DriversList */}
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

              {menuOrigin === 'telemetry' && selectedSessionKey && (
                <div className="drivers-side-panel">
                    <DriversList
                      sessionKey={selectedSessionKey}
                      onDriverSelect={handleDriverSelect}
                      selectedDriverNumber={selectedDriverNumber}
                    />
                </div>
              )}
            </div>

            {/* Segunda linha do Grid: CircuitMapPanel (se for 'races') ou TrackMap (se for 'telemetry') */}
            {menuOrigin === 'races' && selectedSessionKey && circuitRef && (
              <CircuitMapPanel
                circuitref={circuitRef}
                selectedSessionKey={selectedSessionKey}
                circuitShortName={selectedCircuitShortName}
              />
            )}

            {menuOrigin === 'races' && selectedSessionKey && (
              <SessionResultsPanel sessionKey={selectedSessionKey} />
            )}

            {menuOrigin === 'races' && selectedSessionKey && (
              <RaceControl sessionKey={selectedSessionKey} />
            )}

            {/* NOVO: Renderização condicional para TrackMap */}
            {menuOrigin === 'telemetry' && selectedSessionKey && selectedDriverNumber && (
              <div className="telemetry-display-panel"> {/* Novo container para telemetria */}
                <TrackMap
                  sessionKey={selectedSessionKey}
                  selectedDriver={selectedDriverNumber} // Passa o número do driver
                  startDate={selectedSessionStartDate} // Passa a data de início da sessão
                  endDate={selectedSessionEndDate}     // Passa a data de fim da sessão
                />
              </div>
            )}

          </div>
        )}
      </main>
    </div>
  );
}

export default App;