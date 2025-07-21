/* G:\Learning\F1Data\F1Data_Web\src\reportWebVitals.js V1 */
import { API_BASE_URL } from './api'; // ajuste o caminho se necessário
console.log('API_BASE_URL:', API_BASE_URL);

const reportWebVitals = onPerfEntry => {
  if (onPerfEntry && onPerfEntry instanceof Function) {
    import('web-vitals').then(({ getCLS, getFID, getFCP, getLCP, getTTFB }) => {
      getCLS(onPerfEntry);
      getFID(onPerfEntry);
      getFCP(onPerfEntry);
      getLCP(onPerfEntry);
      getTTFB(onPerfEntry);
    });
  }
};

export default reportWebVitals;