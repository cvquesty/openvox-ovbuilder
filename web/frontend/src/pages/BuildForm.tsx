import { useState, useEffect } from 'react';
import {
  Stack, Title, Text, TextInput, NumberInput, Select, TagsInput, Button,
  Group, Card, Divider, Alert, Loader, Center, Switch,
} from '@mantine/core';
import { IconRocket, IconCheck, IconAlertCircle } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { builds, inventory, type BuildRequest, type OsImage, type Environment } from '../services/api';
import { useNavigate } from 'react-router';

const DEFAULTS = {
  cpus: 2,
  memory_gb: 4,
  disk_gb: 80,
  prefix: '24',
};

export function BuildFormPage() {
  const navigate = useNavigate();
  const [osImages, setOsImages] = useState<OsImage[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [form, setForm] = useState<BuildRequest>({
    hostname: '',
    ip: '',
    os_image: 'ubuntu-24.04',
    prefix: DEFAULTS.prefix,
    cpus: DEFAULTS.cpus,
    memory_gb: DEFAULTS.memory_gb,
    disk_gb: DEFAULTS.disk_gb,
    gateway: '',
    dns: [],
    environment: 'dev',
    location: '',
    skip_dnf_groups: false,
  });

  useEffect(() => {
    Promise.all([inventory.osImages(), inventory.environments()])
      .then(([os, envs]) => {
        setOsImages(os);
        setEnvironments(envs);
        if (os.length && !os.find((o) => o.key === form.os_image)) {
          setForm((f) => ({ ...f, os_image: os[0].key }));
        }
        if (envs.length && !envs.find((e) => e.key === form.environment)) {
          setForm((f) => ({ ...f, environment: envs[0].key }));
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const update = <K extends keyof BuildRequest>(key: K, value: BuildRequest[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const job = await builds.submit(form);
      notifications.show({
        title: 'Build queued',
        message: `${form.hostname} is in the queue`,
        color: 'green',
        icon: <IconCheck size={16} />,
      });
      navigate(`/jobs/${job.id}`);
    } catch (err: any) {
      setError(err.message || 'Failed to submit build');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return <Center style={{ minHeight: 300 }}><Loader color="ovred" /></Center>;
  }

  return (
    <Stack gap="lg" maw={640}>
      <div>
        <Title order={2} style={{ letterSpacing: '-0.02em' }}>Build a VM</Title>
        <Text size="sm" c="dimmed" mt={4}>Pick your options and submit — the cluster handles placement.</Text>
      </div>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} title="Error">{error}</Alert>
      )}

      <Card shadow="sm" padding="lg" radius="md" withBorder>
        <form onSubmit={handleSubmit}>
          <Stack gap="md">
            <Group grow>
              <TextInput
                label="Hostname"
                placeholder="web-01"
                value={form.hostname}
                onChange={(e) => update('hostname', e.currentTarget.value)}
                required
              />
              <TextInput
                label="IP Address"
                placeholder="10.0.1.50"
                value={form.ip}
                onChange={(e) => update('ip', e.currentTarget.value)}
                required
              />
            </Group>

            <Group grow>
              <Select
                label="Operating System"
                data={osImages.map((o) => ({ value: o.key, label: o.label }))}
                value={form.os_image}
                onChange={(v) => v && update('os_image', v)}
                required
              />
              <Select
                label="Environment"
                data={environments.map((e) => ({ value: e.key, label: e.label }))}
                value={form.environment}
                onChange={(v) => v && update('environment', v)}
                required
              />
            </Group>

            <Text size="xs" c="dimmed">
              Placement is automatic — dev goes to the dev datastore cluster, prod to prod. You never pick a LUN.
            </Text>

            <Divider label="Resources" labelPosition="center" />

            <Group grow>
              <NumberInput label="CPUs" min={1} max={64} value={form.cpus} onChange={(v) => update('cpus', Number(v) || 1)} />
              <NumberInput label="Memory (GB)" min={1} max={512} value={form.memory_gb} onChange={(v) => update('memory_gb', Number(v) || 1)} />
              <NumberInput label="Disk (GB)" min={10} max={4096} value={form.disk_gb} onChange={(v) => update('disk_gb', Number(v) || 10)} />
            </Group>

            <Divider label="Network" labelPosition="center" />

            <Group grow>
              <TextInput label="Gateway" placeholder="10.0.1.1" value={form.gateway || ''} onChange={(e) => update('gateway', e.currentTarget.value)} />
              <TextInput label="Prefix" placeholder="24" value={form.prefix || ''} onChange={(e) => update('prefix', e.currentTarget.value)} />
            </Group>

            <TagsInput
              label="DNS Servers"
              placeholder="Add DNS servers"
              value={form.dns || []}
              onChange={(v) => update('dns', v)}
            />

            <TextInput label="Location tag" placeholder="optional" value={form.location || ''} onChange={(e) => update('location', e.currentTarget.value)} />

            <Switch
              label="Skip DNF groups (faster AlmaLinux builds)"
              checked={form.skip_dnf_groups || false}
              onChange={(e) => update('skip_dnf_groups', e.currentTarget.checked)}
            />

            <Group justify="flex-end" mt="sm">
              <Button type="submit" loading={submitting} leftSection={<IconRocket size={18} />} color="ovred" size="md">
                Build VM
              </Button>
            </Group>
          </Stack>
        </form>
      </Card>
    </Stack>
  );
}
