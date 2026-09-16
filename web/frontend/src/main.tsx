import React from 'react';
import ReactDOM from 'react-dom/client';
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { BrowserRouter } from 'react-router';
import { App } from './App';
import { AuthProvider } from './hooks/AuthContext';
import { ThemeProvider, useAppTheme } from './hooks/ThemeContext';
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
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </MantineProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <ThemedApp />
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>
);
