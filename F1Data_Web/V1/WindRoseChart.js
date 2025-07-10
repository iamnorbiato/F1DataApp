// src/components/WindRoseChart.js
import React, { useEffect, useRef } from 'react';
import { Chart, registerables } from 'chart.js';

Chart.register(...registerables);

function WindRoseChart({ weatherData }) {
  const chartRef = useRef(null); // Ref para o elemento canvas
  const chartInstanceRef = useRef(null); // Ref para a instância do Chart.js
  const animationFrameIdRef = useRef(null); // Ref para o ID do requestAnimationFrame
  const rotationRef = useRef(0); // NOVO: Ref para armazenar o valor da rotação atual

  const rotationSpeed = 0.1; // Velocidade da rotação (graus por frame). Ajuste este valor.

  // Define a função de animação fora do useEffect para que ela não seja recriada a cada render
  const animateChart = () => {
    // Só anima se a instância do gráfico existir
    if (chartInstanceRef.current) {
      rotationRef.current += rotationSpeed; // Atualiza o valor da rotação na ref
      if (rotationRef.current >= 360) {
        rotationRef.current -= 360;
      }
      // Atualiza a opção de rotação do gráfico e força a atualização visual
      chartInstanceRef.current.options.rotation = rotationRef.current;
      chartInstanceRef.current.update();

      // Solicita o próximo frame da animação
      animationFrameIdRef.current = requestAnimationFrame(animateChart);
    } else {
      // Se o gráfico não existe, garante que a animação seja parada
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
        animationFrameIdRef.current = null;
      }
    }
  };

  // Efeito principal para criar/atualizar/destruir o gráfico
  useEffect(() => {
    // Se não houver dados ou o canvas não estiver pronto, destrói o gráfico e sai
    if (!weatherData || weatherData.length === 0 || !chartRef.current) {
      if (chartInstanceRef.current) {
        chartInstanceRef.current.destroy();
        chartInstanceRef.current = null;
      }
      // Cancela qualquer animação que possa estar rodando
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
        animationFrameIdRef.current = null;
      }
      return;
    }

    const binCount = 12; // 12 setores = 30 graus cada
    const bins = new Array(binCount).fill(0);

    for (const entry of weatherData) {
      const direction = entry.wind_direction ?? 0;
      const normalizedDirection = (direction % 360 + 360) % 360; // Garante valor positivo
      const binIndex = Math.floor(normalizedDirection / (360 / binCount));
      bins[binIndex]++;
    }

    const labels = Array.from({ length: binCount }, (_, i) => {
      const start = i * 30;
      const end = start + 30;
      return `${start}°-${end}°`;
    });

    const data = {
      labels,
      datasets: [
        {
          label: 'Frequência de Vento',
          data: bins,
          backgroundColor: 'rgba(0, 255, 0, 0.6)',
        },
      ],
    };

    const config = {
      type: 'polarArea',
      data,
      options: {
        responsive: true,
        animation: {
          duration: 0, // Desabilita a animação inicial do Chart.js
        },
        scales: {
          r: {
            beginAtZero: true,
            ticks: {
              backdropColor: 'transparent',
            },
            grid: {
              color: 'rgba(255, 255, 255, 0.1)',
            },
          },
        },
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            enabled: true,
          },
        },
        rotation: rotationRef.current, // Define a rotação inicial usando o valor da ref
      },
    };

    const ctx = chartRef.current.getContext('2d');

    // Destroi a instância anterior do gráfico se existir e cria uma nova
    if (chartInstanceRef.current) {
      chartInstanceRef.current.destroy();
    }
    chartInstanceRef.current = new Chart(ctx, config);

    // Inicia o loop de animação para o NOVO gráfico
    // Cancela qualquer animação anterior para garantir que apenas um loop esteja ativo
    if (animationFrameIdRef.current) {
      cancelAnimationFrame(animationFrameIdRef.current);
    }
    animationFrameIdRef.current = requestAnimationFrame(animateChart);


    // Retorna uma função de limpeza para o useEffect
    return () => {
      // Garante que o gráfico e a animação sejam limpos quando o componente for desmontado
      if (chartInstanceRef.current) {
        chartInstanceRef.current.destroy();
        chartInstanceRef.current = null;
      }
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
        animationFrameIdRef.current = null;
      }
    };
  }, [weatherData]); // Dependência: re-executa quando weatherData muda

  return (
    <div className="windrose-chart-container">
      <canvas ref={chartRef}></canvas>
    </div>
  );
}

export default WindRoseChart;