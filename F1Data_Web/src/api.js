// G:\Learning\F1Data\F1Data_Web\src\api.js

export const getApiBaseUrl = () => {
  const origin = window.location.origin;

  const apiPort =
    origin.includes(':7000') ? '7001' :   // externo
    origin.includes(':30080') ? '30081' : // nginx local
    origin.includes(':3000') ? '30081' :  // dev server
    '7001'; // fallback

  return origin.replace(/:\d+$/, `:${apiPort}`);
};

export const API_BASE_URL = getApiBaseUrl();
