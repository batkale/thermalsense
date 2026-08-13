import { createContext, useContext, useState, useEffect, useMemo } from 'react';
import { translate, localeOf, windDirLabel, LANGS, DEFAULT_LANG } from './strings.js';

const STORAGE_KEY = 'thermalsense.lang';
const LanguageContext = createContext(null);

function initialLang() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (LANGS.includes(saved)) return saved;
  } catch { /* storage blocked — fall through to default */ }
  return DEFAULT_LANG;
}

export function LanguageProvider({ children }) {
  const [lang, setLang] = useState(initialLang);

  useEffect(() => {
    document.documentElement.lang = lang;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch { /* non-fatal */ }
  }, [lang]);

  const value = useMemo(() => ({
    lang,
    setLang,
    toggleLang: () => setLang(l => (l === 'tr' ? 'en' : 'tr')),
    t:          (key, vars) => translate(lang, key, vars),
    locale:     localeOf(lang),
    windDir:    (deg) => windDirLabel(lang, deg),
  }), [lang]);

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLang() {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error('useLang must be used inside <LanguageProvider>');
  return ctx;
}
