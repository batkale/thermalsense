import React from 'react';
import ReactDOM from 'react-dom/client';
import 'leaflet/dist/leaflet.css';
// The landing page's two faces, so both pages read as one product: Archivo
// (latin-ext carries ğ, ş, İ) for everything, B612 Mono for figures only.
import '@fontsource-variable/archivo/wdth.css';
import '@fontsource/b612-mono/400.css';
import './index.css';
import App from './App.jsx';
import { LanguageProvider } from './i18n/LanguageContext.jsx';
import { ThemeProvider } from './theme/ThemeContext.jsx';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider>
      <LanguageProvider>
        <App />
      </LanguageProvider>
    </ThemeProvider>
  </React.StrictMode>
);
