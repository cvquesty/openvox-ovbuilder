import { useState, useEffect, useRef } from 'react';
import {
  Center, Card, Title, TextInput, PasswordInput, Button, Alert, Stack, Text, Loader,
} from '@mantine/core';
import { IconLock } from '@tabler/icons-react';
import { useAuth } from '../hooks/AuthContext';
import { useAppTheme } from '../hooks/ThemeContext';
import { useNavigate, useLocation } from 'react-router';

export function LoginPage() {
  const { login, user, loading } = useAuth();
  const { isDark } = useAppTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const errorRef = useRef<HTMLDivElement>(null);
  const usernameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!loading && user) {
      const from = (location.state as any)?.from?.pathname
        || (user.role === 'admin' || user.role === 'builder' ? '/build' : '/jobs');
      navigate(from, { replace: true });
    }
  }, [user, loading, navigate, location]);

  useEffect(() => {
    if (error) {
      errorRef.current?.focus();
    }
  }, [error]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!username.trim()) {
      setError('Username is required');
      usernameRef.current?.focus();
      return;
    }
    if (!password) {
      setError('Password is required');
      return;
    }
    setSubmitting(true);
    try {
      await login(username, password);
      navigate('/', { replace: true });
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return <Center style={{ minHeight: '100vh' }}><Loader /></Center>;
  }

  return (
    <Center
      style={{
        minHeight: '100vh',
        background: !isDark
          ? 'radial-gradient(1200px 500px at 50% -10%, rgba(13,110,253,0.10), transparent 60%), #f3f5f8'
          : 'radial-gradient(1200px 500px at 50% -10%, rgba(236,134,34,0.16), transparent 55%), #12131c',
      }}
    >
      <Card shadow="lg" padding="xl" radius="lg" style={{ width: 400, border: '1px solid var(--ov-line)' }}>
        <Stack align="center" mb="lg" gap="xs">
          <img src="/openvox-logo-red.svg" alt="OpenVox" style={{ height: 56 }} />
          <Title order={2} mt="sm" style={{ letterSpacing: '-0.03em' }}>OV Builder</Title>
          <Text size="sm" c="dimmed">Self-service VM provisioning</Text>
        </Stack>

        <form onSubmit={handleSubmit}>
          <Stack>
            {error && (
              <Alert
                ref={errorRef}
                tabIndex={-1}
                color="red"
                title="Login Failed"
                withCloseButton
                onClose={() => setError(null)}
              >
                <Text size="sm" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{error}</Text>
              </Alert>
            )}
            <TextInput
              ref={usernameRef}
              label="Username"
              placeholder="Enter your username"
              value={username}
              onChange={(e) => setUsername(e.currentTarget.value)}
              required
              autoFocus
              autoComplete="username"
              size="md"
            />
            <PasswordInput
              label="Password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              required
              autoComplete="current-password"
              size="md"
            />
            <Button type="submit" fullWidth loading={submitting} size="md" mt="sm" leftSection={<IconLock size={18} />}>
              Sign In
            </Button>
          </Stack>
        </form>

        <Text size="xs" c="dimmed" ta="center" mt="lg">OpenVox OV Builder v{__APP_VERSION__}</Text>
      </Card>
    </Center>
  );
}
