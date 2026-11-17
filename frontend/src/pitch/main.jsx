import React from 'react';
import ReactDOM from 'react-dom/client';
// Archivo with its width axis (62–125%): one family, stretched wide for display
// and left at normal width for reading. Its latin-ext subset carries ğ, ş and İ.
import '@fontsource-variable/archivo/wdth.css';
// B612 Mono — the face Airbus commissioned for cockpit displays — for figures.
// Latin only: it has no ğ, ş or İ, so it is never applied to translated text.
import '@fontsource/b612-mono/400.css';
import '../landing/landing.css';
import './pitch.css';
import Pitch from './Pitch.jsx';
import { LanguageProvider } from '../i18n/LanguageContext.jsx';
import { ThemeProvider } from '../theme/ThemeContext.jsx';

// The same two providers as the map app, so language and theme are one choice
// across both pages — they share the app's localStorage keys.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider>
      <LanguageProvider>
        <Pitch />
      </LanguageProvider>
    </ThemeProvider>
  </React.StrictMode>
);
