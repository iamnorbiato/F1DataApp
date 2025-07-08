// G:\Learning\F1Data\F1Data_Web\src\DriversList.js
import React, { useState, useEffect } from 'react';

function DriversList({ sessionKey }) {
  const [drivers, setDrivers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Define a URL base da API. Prioriza a variável de ambiente ou usa localhost como fallback.
  const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:30080';

  useEffect(() => {
    // Só tenta buscar dados se um sessionKey for fornecido
    if (sessionKey) {
      const fetchDrivers = async () => {
        setLoading(true); // Inicia o estado de carregamento
        setError(null);   // Limpa qualquer erro anterior
        try {
          // Constrói a URL da API para buscar drivers por session_key
          const apiUrl = `${API_BASE_URL}/api/drivers-by-session/?session_key=${sessionKey}`;
          console.log('DEBUG DriversList: URL da API de drivers:', apiUrl);

          const response = await fetch(apiUrl);

          // Verifica se a resposta da rede foi bem-sucedida
          if (!response.ok) {
            throw new Error(`Erro HTTP: ${response.status} ${response.statusText}`);
          }

          const data = await response.json(); // Converte a resposta para JSON
          console.log('DEBUG DriversList: Dados de drivers recebidos:', data);

          setDrivers(data); // Atualiza o estado com os drivers recebidos
        } catch (err) {
          console.error('Erro ao buscar drivers:', err);
          setError(err.message || 'Erro ao carregar drivers.'); // Define a mensagem de erro
        } finally {
          setLoading(false); // Finaliza o estado de carregamento
        }
      };
      fetchDrivers();
    } else {
      // Se não houver sessionKey, limpa a lista de drivers e desativa o carregamento
      setDrivers([]); 
      setLoading(false);
    }
  }, [sessionKey, API_BASE_URL]); // O efeito re-executa quando sessionKey ou API_BASE_URL mudam

  // Renderização condicional baseada nos estados de carregamento e erro
  if (loading) {
    return <p>Carregando drivers...</p>;
  }

  if (error) {
    return <p style={{ color: 'red' }}>Erro: {error}</p>;
  }

  if (drivers.length === 0) {
    // Mensagem exibida quando não há drivers ou sessionKey não foi selecionado
    return <p>Nenhum driver encontrado para esta sessão.</p>;
  }

  // Renderiza a lista de drivers
  return (
    <div className="drivers-list-container"> {/* Container principal que terá a altura controlada */}
      <div className="drivers-table-header">
        <span>Num</span>
        <span>Nome</span>
        <span>Time</span>
      </div>
      <div className="drivers-list-scrollable"> {/* Esta é a área que terá o scroll */}
        {drivers.map(driver => (
          <div key={driver.driver_number} className="driver-list-item">
            <span>{driver.driver_number}</span>
            {/*<span>{driver.full_name} ({driver.country_code})</span> */}
            <span>{driver.full_name}</span> 
            {/* O team_colour é uma cor hexadecimal, usamos um template literal para aplicá-lo como estilo inline */}
            <span style={{ color: `#${driver.team_colour}` }}>{driver.team_name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default DriversList;