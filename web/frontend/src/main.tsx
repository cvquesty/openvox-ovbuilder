import React from 'react';
import ReactDOM from 'react-dom/client';
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { BrowserRouter } from 'react-router';
import { App } from './App';
import { AuthProvider } from './hooks/AuthContext';
import { ThemeProvider, useAppTheme } from './hooks/ThemeContext';
import { ErrorBoundary } from './components/ErrorBoundary';
import { lightTheme, darkTheme } from './theme';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import './styles/app.css';

function ThemedApp() {
  const { theme } = useAppTheme();
  const mantineTheme = theme === 'light' ? lightTheme : darkTheme;
  return (
    <MantineProvider theme={mantineTheme} forceColorScheme={theme}>
      <Notifications position="bottom-right" />
      <AuthProvider>
        <BrowserRouter>
          <ErrorBoundary>
            <App />
          </ErrorBoundary>
        </BrowserRouter>
      </AuthProvider>
    </MantineProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  </React.StrictMode>
);
