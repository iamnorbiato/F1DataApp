// G:\Learning\F1Data\F1Data_Web\src\Sidebar.js
import React from 'react';

function Sidebar({ onMenuItemClick, activeMenuItem }) {
  const menuItems = [
    { name: 'races', label: "Grande Premio" },
    { name: 'teams', label: 'Equipes' },
    { name: 'drivers', label: 'Pilotos' },
    { name: 'circuits', label: 'Circuitos' },
    { name: 'telemetry', label: 'Telemetria' },
    { name: 'settings', label: 'Configurações' },
  ];

  return (
    <div className="sidebar-container">
      <h2>F1Data Dashboard</h2>
      <ul className="menu-list">
        {menuItems.map((item) => (
          <li key={item.name} className="menu-item-wrapper">
            <button
              className={`menu-item-button ${activeMenuItem === item.name ? 'active' : ''}`}
              onClick={() => onMenuItemClick(item.name, item.label)}
            >
              {item.label}
            </button>
            {/* O OptionChooserFrame será renderizado por App.js se activeMenuItem corresponder */}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default Sidebar;