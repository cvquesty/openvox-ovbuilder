import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import type { AppTheme } from '../theme';

interface ThemeContextType {
  theme: AppTheme;
  isDark: boolean;
  setTheme: (t: AppTheme) => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextType>({
  theme: 'light',
  isDark: false,
  setTheme: () => {},
  toggle: () => {},
});

export function useAppTheme() {
  return useContext(ThemeContext);
}

function preferredTheme(): AppTheme {
  try {
    const stored = localStorage.getItem('ovbuilder-theme');
    if (stored === 'dark' || stored === 'light') return stored;
  } catch { /* ignore */ }
  try {
    if (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      return 'dark';
    }
  } catch { /* ignore */ }
  return 'light';
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<AppTheme>(() => preferredTheme());

  useEffect(() => {
    try { localStorage.setItem('ovbuilder-theme', theme); } catch { /* ignore */ }
    document.documentElement.setAttribute('data-mantine-color-scheme', theme);
  }, [theme]);

  const setTheme = (t: AppTheme) => setThemeState(t);
  const toggle = () => setThemeState((prev) => (prev === 'light' ? 'dark' : 'light'));

  return (
    <ThemeContext.Provider value={{ theme, isDark: theme === 'dark', setTheme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}
