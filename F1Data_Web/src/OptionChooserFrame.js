// G:\Learning\F1Data\F1Data_Web\src\OptionChooserFrame.js
import React, { useState, useEffect, useRef } from 'react';

function Dropdown({ label, options, selectedValue, onSelect, placeholder }) {
  return (
    <div className="dropdown-group">
      <label>{label}</label>
      <select value={selectedValue || ''} onChange={(e) => onSelect(e.target.value)}>
        <option value="" disabled>{placeholder}</option>
        {options.map(option => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </div>
  );
}

function OptionChooserFrame({ menuItemName, onClose, menuItemLabel, onSearchData }) { 
  const [availableYears, setAvailableYears] = useState([]);
  const [meetingsByYear, setMeetingsByYear] = useState([]);
  const [selectedYear, setSelectedYear] = useState(null);
  const [selectedMeeting, setSelectedMeeting] = useState(null);

  const frameRef = useRef(null);
  const [frameStyle, setFrameStyle] = useState({});

  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:30080';

  // Efeito para posicionar o frame
  useEffect(() => {
    const activeMenuButton = document.querySelector(`.menu-item-button.active`);
    if (activeMenuButton && frameRef.current) {
      const buttonRect = activeMenuButton.getBoundingClientRect();
      const sidebarRect = activeMenuButton.closest('.sidebar-container').getBoundingClientRect();

      setFrameStyle({
        top: buttonRect.bottom - sidebarRect.top + 5 + 'px',
        left: buttonRect.left - sidebarRect.left + 'px',
        width: buttonRect.width + 'px',
      });
    }
  }, [menuItemName, menuItemLabel]);

  // Efeito para carregar os anos disponíveis da API
  useEffect(() => {
    const fetchYears = async () => {
      try {
        console.log('DEBUG: Chamando API para buscar anos em:', `${API_BASE_URL}/api/filters/meetings/`);
        const response = await fetch(`${API_BASE_URL}/api/filters/meetings/`);
        const data = await response.json();
        console.log('DEBUG: Dados de anos recebidos:', data); 
        if (data && data.available_years) {
          const yearsOptions = data.available_years.map(year => ({ value: year, label: String(year) }));
          setAvailableYears(yearsOptions);
          console.log('DEBUG: availableYears após setAvailableYears:', yearsOptions);
        }
      } catch (error) {
        console.error('Erro ao buscar anos:', error);
      }
    };
    fetchYears();
  }, [API_BASE_URL]); 

  // Efeito para carregar meetings quando um ano é selecionado
  useEffect(() => {
    if (selectedYear) {
      const fetchMeetings = async () => {
        try {
          console.log(`DEBUG: Chamando API para buscar meetings do ano ${selectedYear} em:`, `${API_BASE_URL}/api/filters/meetings/?year=${selectedYear}`);
          const response = await fetch(`${API_BASE_URL}/api/filters/meetings/?year=${selectedYear}`);
          const data = await response.json();
          console.log(`DEBUG: Dados de meetings para ${selectedYear} recebidos:`, data);
          if (data) {
            const meetingsOptions = data.map(meeting => ({ 
              value: meeting.meeting_key, 
              label: meeting.display_name,
              official_name: meeting.meeting_official_name
            }));
            setMeetingsByYear(meetingsOptions);
            console.log('DEBUG: meetingsByYear após setMeetingsByYear:', meetingsOptions);
          }
        } catch (error) {
          console.error(`Erro ao buscar meetings para o ano ${selectedYear}:`, error);
        }
      };
      fetchMeetings();
    } else {
      setMeetingsByYear([]);
    }
  }, [selectedYear, API_BASE_URL]); 

  const handleSelectYear = (year) => {
    setSelectedYear(year);
    setSelectedMeeting(null);
    console.log('DEBUG: Ano selecionado:', year);
  };

  const handleSelectMeeting = (meetingKey) => {
    setSelectedMeeting(meetingKey);
    console.log('DEBUG: Meeting selecionado:', meetingKey);
  };

  // --- CORREÇÃO AQUI: Handler para o botão "Buscar Dados" ---
  const handleSearchButtonClick = () => {
    console.log('DEBUG: Botão Buscar Dados clicado.');
    // CORREÇÃO: Converte selectedMeeting para Number antes de usar no find
    const meetingSelectedObject = meetingsByYear.find(m => m.value === Number(selectedMeeting)); // <--- MUDANÇA AQUI!

    if (meetingSelectedObject && onSearchData) {
      console.log('DEBUG: Chamando onSearchData com:', meetingSelectedObject.value, meetingSelectedObject.official_name);
      onSearchData(meetingSelectedObject.value, meetingSelectedObject.official_name);
    } else {
      console.log('DEBUG: Não é possível buscar dados. Seleção incompleta ou onSearchData não fornecido.');
      // Adicione um aviso visual para o usuário se a seleção não estiver completa
    }
  };
  // --------------------------------------------------

  console.log('DEBUG: availableYears (render):', availableYears);
  console.log('DEBUG: selectedYear (render):', selectedYear);
  console.log('DEBUG: meetingsByYear (render):', meetingsByYear);
  console.log('DEBUG: selectedMeeting (render):', selectedMeeting);

  return (
    <div ref={frameRef} className={`option-chooser-frame ${menuItemName ? 'visible' : ''}`} style={frameStyle}>
      <div className="frame-header">
        <h4>Selecione Opções para: {menuItemLabel}</h4>
        <button className="frame-close-button" onClick={onClose}>X</button>
      </div>

      <Dropdown
        label="Selecione o Ano:"
        options={availableYears}
        selectedValue={selectedYear}
        onSelect={handleSelectYear}
        placeholder="Carregando anos..."
      />

      {selectedYear && (
        <Dropdown
          label="Selecione o Meeting:"
          options={meetingsByYear}
          selectedValue={selectedMeeting}
          onSelect={handleSelectMeeting}
          placeholder="Carregando meetings..."
        />
      )}

      {selectedMeeting && (
        <div className="action-button-container">
          <button className="action-button" onClick={handleSearchButtonClick}>Buscar Dados</button>
        </div>
      )}
    </div>
  );
}

export default OptionChooserFrame;