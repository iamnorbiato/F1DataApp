// G:\Learning\F1Data\F1Data_Web\src\App.js
import React, { useState, useEffect, useRef } from 'react';
import './App.css';
import MeetingsList from './MeetingsList';
import Sessions from './Sessions';
import CircuitMapPanel from './CircuitMapPanel';
import SessionResultsPanel from './SessionResultsPanel';
import RaceControl from './RaceControl';
import DriversList from './DriversList';
import TrackMap from './TrackMap';
import { API_BASE_URL } from './api';

// Função para converter uma data para ISO string UTC com "Z" no final
function toUTCISOString(dateStr) {
  if (!dateStr) return null;
  const date = new Date(dateStr);
  if (isNaN(date)) return null; // Verifica data inválida
  return date.toISOString();
}

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
  const [selectedDriverObject, setSelectedDriverObject] = useState(null);
  // REMOVIDO: Estados para as datas MIN e MAX de Location foram movidos para o TrackMap
  // const [locationMinDate, setLocationMinDate] = useState(null);
  // const [locationMaxDate, setLocationMaxDate] = useState(null);

  const handleHamburgerClick = () => {
    setIsMobileMenuOpen(!isMobileMenuOpen);
  };

  const handleCloseMobileMenu = () => {
    setIsMobileMenuOpen(false);
    setShowYearDropdown(false);
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
    // REMOVIDO: reset de datas de location
    setCircuitRef(null);
    setSelectedDriverNumber(null);
    setSelectedDriverObject(null);

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
    // REMOVIDO: reset de datas de location
    setCircuitRef(null);
    setSelectedDriverNumber(null);
    setSelectedDriverObject(null);
    setShowYearDropdown(false);
    if (isMobileMenuOpen) setIsMobileMenuOpen(false);
  };

  const handleMeetingSelect = async (meetingKey, meetingName, circuitShortName, circuitKeyForCircuit) => {
    setSelectedMeetingKey(meetingKey);
    setSelectedMeetingName(meetingName);
    setSelectedCircuitShortName(circuitShortName);
    setSelectedSessionKey(null);
    // REMOVIDO: reset de datas de location
    setSelectedDriverNumber(null);
    setSelectedDriverObject(null);

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

  const handleSessionSelect = (sessionKey, sessionName, dateStart, dateEnd) => {
    setSelectedSessionKey(sessionKey);
    // REMOVIDO: O TrackMap agora será responsável por buscar as datas de location
    setSelectedDriverNumber(null);
    setSelectedDriverObject(null); 
    console.log(`Sessão selecionada: ${sessionKey}, ${sessionName} (Origem: ${menuOrigin})`);
  };

  const handleDriverSelect = async (driverObject) => {
    setSelectedDriverNumber(driverObject.driver_number);
    setSelectedDriverObject(driverObject);
    console.log(`Driver selecionado: ${driverObject.driver_number}`);
    // REMOVIDO: A chamada da API para min-max-location-date foi movida para o TrackMap
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
          menuOrigin === 'races' ? (
            <div className="races-layout-container">
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
              {selectedSessionKey && (
                <SessionResultsPanel sessionKey={selectedSessionKey} />
              )}
              {selectedSessionKey && circuitRef && (
                <CircuitMapPanel
                  circuitref={circuitRef}
                  selectedSessionKey={selectedSessionKey}
                  circuitShortName={selectedCircuitShortName}
                />
              )}
              {selectedSessionKey && (
                <RaceControl sessionKey={selectedSessionKey} />
              )}
            </div>
          ) : menuOrigin === 'telemetry' ? (
            <div className="telemetry-layout-container">
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
              {selectedSessionKey && (
                <div className="drivers-side-panel">
                    <DriversList
                      sessionKey={selectedSessionKey}
                      onDriverSelect={handleDriverSelect}
                      selectedDriverNumber={selectedDriverNumber}
                    />
                </div>
              )}
              {selectedSessionKey && selectedDriverObject && (
                <div className="telemetry-display-panel">
                  <TrackMap
                    sessionKey={selectedSessionKey}
                    selectedDriver={selectedDriverObject}
                    // REMOVIDO: As datas agora são buscadas dentro do TrackMap
                    // startDate={locationMinDate}
                    // endDate={locationMaxDate}
                  />
                </div>
              )}
            </div>
          ) : (
            null
          )
        )}
      </main>
    </div>
  );
}

export default App;