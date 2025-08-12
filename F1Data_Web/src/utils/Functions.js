// G:\Learning\F1Data\F1Data_Web\src\utils\Functions.js
import { useState, useRef } from 'react';

export const useDriverHoverCard = () => {
  const [hoveredHeadshotUrl, setHoveredHeadshotUrl] = useState(null);
  const [mouseX, setMouseX] = useState(0);
  const [mouseY, setMouseY] = useState(0);

  const lastUpdate = useRef(0);

  const handleMouseEnter = (url) => {
    setHoveredHeadshotUrl(url);
  };

  const handleMouseLeave = () => {
    setHoveredHeadshotUrl(null);
  };

  const handleMouseMove = (event) => {
    const now = Date.now();
    if (now - lastUpdate.current > 50) { // Atualiza no máximo a cada 50ms
      setMouseX(event.clientX);
      setMouseY(event.clientY);
      lastUpdate.current = now;
    }
  };

  return {
    hoveredHeadshotUrl,
    mouseX,
    mouseY,
    handleMouseEnter,
    handleMouseLeave,
    handleMouseMove
  };
};
