/* src/components/DashboardPanel.js - V1.0.0 */
import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from './api'; // ajuste o caminho se necessário
console.log('API_BASE_URL:', API_BASE_URL);

const F1SoftTire = '../public/Tyres/F1SoftTire.png'; // Importe a imagem do pneu

// Componente para o Gauge (Medidor Circular)
const Gauge = ({ label, value, min, max, unit }) => {
    // Calcula o percentual para preencher o arco
    const percentage = ((value - min) / (max - min)) * 100;
    const strokeDashoffset = 188.5 - (188.5 * percentage) / 100; // 188.5 é a circunferência do círculo

    return (
        <div className="gauge-container">
            <svg viewBox="0 0 100 100" className="gauge-svg">
                <circle cx="50" cy="50" r="30" className="gauge-background"></circle>
                <circle
                    cx="50"
                    cy="50"
                    r="30"
                    className="gauge-progress"
                    style={{ strokeDashoffset }}
                ></circle>
                <text x="50" y="50" textAnchor="middle" className="gauge-value">
                    {value}
                </text>
            </svg>
            <div className="gauge-label">{label}</div>
            <div className="gauge-unit">{unit}</div>
        </div>
    );
};

const DashboardPanel = () => {
    // Dados mocados para o Dashboard
    const dashboardData = {
        n_gear: 6,
        throttle: 80, // Valor de 0 a 100
        brake: 20,    // Valor de 0 a 100
        lap: 22,
        position: 20,
        speed: 280, // Valor para o gauge
        rpm: 12000, // Valor para o gauge
        is_drs_active: true,
        tire: 'SOFT',
        lap_data: [
            { lap: 1, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "00.638", interval: "0.120" },
            { lap: 2, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "02.125", interval: "0.350" },
            { lap: 3, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "12.243", interval: "0.125" },
            { lap: 4, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 5, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 6, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 7, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 8, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 9, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 10, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 11, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 12, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
            { lap: 13, time: "1:23.981", sector1: "30.189", sector2: "31.074", sector3: "30.178", to_leader: "+1Lap", interval: "0.350" },
        ]
    };

    return (
        <div className="dashboard-panel-wrapper">
            <div className="dashboard-top-indicators">
                <div className="gear-bars-container">
                    <span className="gear-number">{dashboardData.n_gear}</span>
                    <div className="bars-container">
                        <div className="throttle-bar" style={{ height: `${dashboardData.throttle}%` }}></div>
                        <div className="brake-bar" style={{ height: `${dashboardData.brake}%` }}></div>
                    </div>
                </div>

                <div className="tire-lap-info-container">
                    <div className="tire-container">
                        <img src={F1SoftTire} alt="F1 Soft Tire" className="tire-image" />
                    </div>
                    <div className="lap-info-container">
                        <span className="lap-text">L{dashboardData.lap}</span>
                        <span className="position-text">P{dashboardData.position}</span>
                    </div>
                </div>

                <div className="gauges-drs-container">
                    <div className="drs-container">
                        <span className={`drs-indicator ${dashboardData.is_drs_active ? 'active' : ''}`}>DRS</span>
                    </div>
                    <div className="gauges-container">
                        <Gauge label="KM/H" value={dashboardData.speed} min={0} max={350} unit="" />
                        <Gauge label="RPM" value={dashboardData.rpm} min={0} max={15000} unit="" />
                    </div>
                </div>
            </div>

            <div className="dashboard-laps-table">
                <div className="dashboard-table-header">
                    <span className="header-lap">Lap</span>
                    <span className="header-time">Time</span>
                    <span className="header-sector">Sector 1</span>
                    <span className="header-sector">Sector 2</span>
                    <span className="header-sector">Sector 3</span>
                    <span className="header-to-leader">to leader</span>
                    <span className="header-interval">Interval</span>
                </div>
                <ul className="dashboard-laps-list">
                    {dashboardData.lap_data.map((lap, index) => (
                        <li key={index} className="dashboard-lap-item">
                            <span className="lap-number">L{lap.lap}</span>
                            <span className="lap-time">{lap.time}</span>
                            <span className="lap-sector">{lap.sector1}</span>
                            <span className="lap-sector">{lap.sector2}</span>
                            <span className="lap-sector">{lap.sector3}</span>
                            <span className="lap-to-leader">{lap.to_leader}</span>
                            <span className="lap-interval">{lap.interval}</span>
                        </li>
                    ))}
                </ul>
            </div>
        </div>
    );
};

export default DashboardPanel;