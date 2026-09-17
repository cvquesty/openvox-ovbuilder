import { Routes, Route, Navigate } from 'react-router';
import { useAuth } from './hooks/AuthContext';
import { AppShellLayout } from './components/AppShell';
import { LoginPage } from './pages/Login';
import { BuildFormPage } from './pages/BuildForm';
import { JobsPage } from './pages/Jobs';
import { JobDetailPage } from './pages/JobDetail';
import { VMsPage } from './pages/VMs';
import { SettingsPage } from './pages/Settings';
import { Alert, Center, Loader, Stack, Text } from '@mantine/core';
import { IconLock } from '@tabler/icons-react';

function ProtectedRoute({ children, roles }: { children: React.ReactNode; roles?: string[] }) {
  const { user, loading } = useAuth();
  if (loading) return <Center style={{ minHeight: '100vh' }}><Loader /></Center>;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.role)) {
    return (
      <Stack maw={480} mx="auto" mt="xl" p="md">
        <Alert color="orange" icon={<IconLock size={16} />} title="Access denied">
          <Text size="sm">
            Your role ({user.role}) cannot access this page. Contact an administrator if you need access.
          </Text>
        </Alert>
      </Stack>
    );
  }
  return <>{children}</>;
}

function DefaultRedirect() {
  const { user } = useAuth();
  if (user && (user.role === 'admin' || user.role === 'builder')) {
    return <Navigate to="/build" replace />;
  }
  return <Navigate to="/jobs" replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute><AppShellLayout /></ProtectedRoute>}>
        <Route path="/build" element={<ProtectedRoute roles={["admin", "builder"]}><BuildFormPage /></ProtectedRoute>} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/jobs/:id" element={<JobDetailPage />} />
        <Route path="/vms" element={<ProtectedRoute roles={["admin", "builder"]}><VMsPage /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute roles={["admin"]}><SettingsPage /></ProtectedRoute>} />
        <Route path="/" element={<DefaultRedirect />} />
        <Route path="*" element={<DefaultRedirect />} />
      </Route>
    </Routes>
  );
}
