import { Routes, Route, Navigate } from 'react-router';
import { useAuth } from './hooks/AuthContext';
import { AppShellLayout } from './components/AppShell';
import { LoginPage } from './pages/Login';
import { BuildFormPage } from './pages/BuildForm';
import { JobsPage } from './pages/Jobs';
import { JobDetailPage } from './pages/JobDetail';
import { Center, Loader } from '@mantine/core';

function ProtectedRoute({ children, roles }: { children: React.ReactNode; roles?: string[] }) {
  const { user, loading } = useAuth();
  if (loading) return <Center style={{ minHeight: '100vh' }}><Loader color="ovred" /></Center>;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.role)) return <Navigate to="/build" replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute><AppShellLayout /></ProtectedRoute>}>
        <Route path="/build" element={<ProtectedRoute roles={["admin", "builder"]}><BuildFormPage /></ProtectedRoute>} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/jobs/:id" element={<JobDetailPage />} />
        <Route path="/" element={<Navigate to="/build" replace />} />
        <Route path="*" element={<Navigate to="/build" replace />} />
      </Route>
    </Routes>
  );
}
