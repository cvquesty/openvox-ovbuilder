import { useEffect, useState } from 'react';
import { Alert, Card, Center, Group, Loader, Stack, Switch, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconBell } from '@tabler/icons-react';
import { auth, type NotificationPrefs } from '../services/api';
import { useAuth } from '../hooks/AuthContext';

const EVENTS: { key: keyof NotificationPrefs; label: string }[] = [
  { key: 'on_success', label: 'Build succeeded' },
  { key: 'on_failure', label: 'Build failed' },
  { key: 'on_cancelled', label: 'Build cancelled' },
];

export function NotificationPrefsPage() {
  const { user } = useAuth();
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null);
  const [saving, setSaving] = useState<keyof NotificationPrefs | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    auth
      .notificationPrefs()
      .then(setPrefs)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load preferences'));
  }, []);

  const toggle = async (key: keyof NotificationPrefs, value: boolean) => {
    if (!prefs) return;
    const previous = prefs;
    setPrefs({ ...prefs, [key]: value });
    setSaving(key);
    setError(null);
    try {
      setPrefs(await auth.updateNotificationPrefs({ [key]: value }));
      notifications.show({ message: 'Notification preferences saved', color: 'teal' });
    } catch (err) {
      setPrefs(previous);
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(null);
    }
  };

  if (!prefs && !error) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    );
  }

  return (
    <Stack gap="lg" maw={560}>
      <div>
        <Group gap="sm" mb={4}>
          <IconBell size={22} stroke={1.6} />
          <Title order={2}>My notifications</Title>
        </Group>
        <Text c="dimmed" size="sm">
          Email about builds you requested, sent to {user?.email || 'your directory email address'}.
          An administrator must enable email delivery for these to take effect.
        </Text>
      </div>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {prefs && (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="sm">
            <Switch
              label="Email me about my builds"
              checked={prefs.email_enabled}
              disabled={saving !== null}
              onChange={(e) => toggle('email_enabled', e.currentTarget.checked)}
            />
            {EVENTS.map(({ key, label }) => (
              <Switch
                key={key}
                ml="lg"
                label={label}
                checked={prefs[key]}
                disabled={saving !== null || !prefs.email_enabled}
                onChange={(e) => toggle(key, e.currentTarget.checked)}
              />
            ))}
          </Stack>
        </Card>
      )}
    </Stack>
  );
}
