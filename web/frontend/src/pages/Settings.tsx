import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Center,
  Divider,
  Group,
  Loader,
  PasswordInput,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconCheck, IconSettings } from '@tabler/icons-react';
import { settings, type RuntimeSettingsUpdate } from '../services/api';

type FormValues = {
  vsphere_server: string;
  vsphere_user: string;
  vsphere_password: string;
  vsphere_datacenter: string;
  vsphere_ignore_ssl: boolean;
  npm_registry: string;
};

export function SettingsPage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passwordSet, setPasswordSet] = useState(false);
  const errorRef = useRef<HTMLDivElement>(null);

  const form = useForm<FormValues>({
    initialValues: {
      vsphere_server: '',
      vsphere_user: '',
      vsphere_password: '',
      vsphere_datacenter: '',
      vsphere_ignore_ssl: true,
      npm_registry: 'https://registry.npmjs.org/',
    },
    validate: {
      vsphere_server: (v) => {
        const t = (v || '').trim();
        if (!t) return 'vSphere server is required';
        return null;
      },
      npm_registry: (v) => {
        const t = (v || '').trim();
        if (!t) return 'npm registry URL is required';
        try {
          const u = new URL(t);
          if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'Use http:// or https://';
        } catch {
          return 'Enter a valid URL';
        }
        return null;
      },
    },
  });

  useEffect(() => {
    settings
      .get()
      .then((data) => {
        setPasswordSet(data.vsphere_password_set);
        form.setValues({
          vsphere_server: data.vsphere_server || '',
          vsphere_user: data.vsphere_user || '',
          vsphere_password: '',
          vsphere_datacenter: data.vsphere_datacenter || '',
          vsphere_ignore_ssl: data.vsphere_ignore_ssl,
          npm_registry: data.npm_registry || 'https://registry.npmjs.org/',
        });
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load settings'))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  const onSubmit = form.onSubmit(async (values) => {
    setSaving(true);
    setError(null);
    const body: RuntimeSettingsUpdate = {
      vsphere_server: values.vsphere_server.trim(),
      vsphere_user: values.vsphere_user.trim(),
      vsphere_datacenter: values.vsphere_datacenter.trim(),
      vsphere_ignore_ssl: values.vsphere_ignore_ssl,
      npm_registry: values.npm_registry.trim(),
    };
    // Only send password when the admin typed something (or cleared intentionally).
    if (values.vsphere_password !== '') {
      body.vsphere_password = values.vsphere_password;
    }
    try {
      const saved = await settings.update(body);
      setPasswordSet(saved.vsphere_password_set);
      form.setFieldValue('vsphere_password', '');
      notifications.show({
        title: 'Settings saved',
        message: 'Configuration updated for OV Builder.',
        color: 'teal',
        icon: <IconCheck size={16} />,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  });

  if (loading) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    );
  }

  return (
    <Stack gap="lg" maw={720}>
      <div>
        <Group gap="sm" mb={4}>
          <IconSettings size={22} stroke={1.6} />
          <Title order={2}>Configuration</Title>
        </Group>
        <Text c="dimmed" size="sm">
          Admin-only settings for vSphere connectivity and package registries. Passwords are stored on the server and never returned to the browser.
        </Text>
      </div>

      {error && (
        <Alert
          ref={errorRef}
          tabIndex={-1}
          color="red"
          icon={<IconAlertCircle size={16} />}
          title="Could not save"
          onClose={() => setError(null)}
          withCloseButton
        >
          {error}
        </Alert>
      )}

      <form onSubmit={onSubmit}>
        <Stack gap="md">
          <Card withBorder padding="lg" radius="md">
            <Title order={4} mb="xs">
              vSphere / VMware
            </Title>
            <Text size="sm" c="dimmed" mb="md">
              Used by inventory and VM lifecycle actions. Leave the password blank to keep the current value.
            </Text>
            <Stack gap="sm">
              <TextInput
                label="Server hostname"
                placeholder="vcenter.example.com"
                autoComplete="off"
                {...form.getInputProps('vsphere_server')}
              />
              <TextInput
                label="Username"
                placeholder="rachel.c@example.org"
                autoComplete="off"
                {...form.getInputProps('vsphere_user')}
              />
              <PasswordInput
                label="Password"
                placeholder={passwordSet ? '••••••••  (leave blank to keep)' : 'Enter password'}
                description={passwordSet ? 'A password is already stored on the server.' : undefined}
                autoComplete="new-password"
                {...form.getInputProps('vsphere_password')}
              />
              <TextInput
                label="Datacenter"
                placeholder="optional"
                autoComplete="off"
                {...form.getInputProps('vsphere_datacenter')}
              />
              <Switch
                label="Ignore SSL certificate errors"
                description="Lab default. Turn off in production when using trusted certs."
                {...form.getInputProps('vsphere_ignore_ssl', { type: 'checkbox' })}
              />
            </Stack>
          </Card>

          <Card withBorder padding="lg" radius="md">
            <Title order={4} mb="xs">
              Package resources
            </Title>
            <Text size="sm" c="dimmed" mb="md">
              Prefer the public npm registry unless you intentionally use a private mirror reachable from this host and CI.
            </Text>
            <TextInput
              label="npm registry URL"
              placeholder="https://registry.npmjs.org/"
              autoComplete="off"
              {...form.getInputProps('npm_registry')}
            />
          </Card>

          <Divider />
          <Group>
            <Button type="submit" loading={saving}>
              Save configuration
            </Button>
          </Group>
        </Stack>
      </form>
    </Stack>
  );
}
