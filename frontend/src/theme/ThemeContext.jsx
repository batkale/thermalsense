import { createContext, useContext, useState, useEffect, useMemo, useCallback } from 'react';

const STORAGE_KEY = 'thermalsense.theme';
export const THEMES = ['dark', 'light'];
export const DEFAULT_THEME = 'dark';

const ThemeContext = createContext(null);

/**
 * Saved choice first, then the operating system, then dark.
 *
 * Mirrored by the inline bootstrap in index.html, which runs this same
 * resolution before the bundle loads so the first paint is already in the right
 * theme. Change one and change the other — a mismatch shows up as a flash of
 * the wrong colours on load, not as an error.
 */
export function resolveInitialTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (THEMES.includes(saved)) return saved;
  } catch { /* storage blocked — fall through to the OS preference */ }
  try {
    if (window.matchMedia?.('(prefers-color-scheme: light)').matches) return 'light';
  } catch { /* no matchMedia — fall through to the default */ }
  return DEFAULT_THEME;
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(resolveInitialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // Only an explicit choice is written. Persisting the resolved initial value
  // would pin a first-time visitor to whatever their OS happened to say on that
  // one load, and quietly stop following it from then on.
  const choose = useCallback((next) => {
    setTheme(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* non-fatal */ }
  }, []);

  const value = useMemo(() => ({
    theme,
    setTheme:    choose,
    toggleTheme: () => choose(theme === 'dark' ? 'light' : 'dark'),
  }), [theme, choose]);

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>');
  return ctx;
}
