import { useState, useEffect, useMemo } from 'react';
import {
  Stack, Title, Text, TextInput, NumberInput, Select, TagsInput, Button,
  Group, Card, Divider, Alert, Loader, Center, Switch,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconRocket, IconCheck, IconAlertCircle } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { builds, inventory, type BuildRequest, type OsImage, type Environment } from '../services/api';
import { useNavigate } from 'react-router';

const DEFAULTS = { cpus: 2, memory_gb: 4, disk_gb: 80, prefix: '24' };

function isValidHostname(value: string): boolean {
  if (!value || value.length > 253) return false;
  const labels = value.split('.');
  if (!labels.length) return false;
  const labelRe = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
  return labels.every((l) => labelRe.test(l));
}
const IPV4_RE = /^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$/;

function isAlmaOs(key: string, label?: string) {
  return `${key} ${label || ''}`.toLowerCase().includes('alma');
}

export function BuildFormPage() {
  const navigate = useNavigate();
  const [osImages, setOsImages] = useState<OsImage[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const form = useForm<BuildRequest>({
    initialValues: {
      hostname: '', ip: '', os_image: 'ubuntu-24.04', prefix: DEFAULTS.prefix,
      cpus: DEFAULTS.cpus, memory_gb: DEFAULTS.memory_gb, disk_gb: DEFAULTS.disk_gb,
      gateway: '', dns: [], environment: 'dev', location: '', skip_dnf_groups: false,
    },
    validate: {
      hostname: (v) => {
        const t = (v || '').trim();
        if (!t) return 'Hostname is required';
        if (!isValidHostname(t)) return 'Invalid hostname';
        return null;
      },
      ip: (v) => {
        const t = (v || '').trim();
        if (!t) return 'IP address is required';
        if (!IPV4_RE.test(t)) return 'Invalid IPv4 address';
        return null;
      },
      prefix: (v) => {
        if (v === undefined || v === null || String(v).trim() === '') return null;
        const n = Number(v);
        if (!Number.isInteger(n) || n < 1 || n > 32) return 'Prefix must be 1\u201332';
        return null;
      },
      gateway: (v) => {
        const t = (v || '').trim();
        if (!t) return null;
        if (!IPV4_RE.test(t)) return 'Invalid IPv4 gateway';
        return null;
      },
      dns: (v) => {
        for (const d of (v || [])) {
          if (!IPV4_RE.test(d.trim())) return `Invalid DNS address: ${d}`;
        }
        return null;
      },
      os_image: (v) => (!v ? 'OS is required' : null),
      environment: (v) => (!v ? 'Environment is required' : null),
    },
    validateInputOnChange: true,
    validateInputOnBlur: true,
  });

  useEffect(() => {
    Promise.all([inventory.osImages(), inventory.environments()])
      .then(([os, envs]) => {
        setOsImages(os);
        setEnvironments(envs);
        if (os.length && !os.find((o) => o.key === form.values.os_image)) {
          form.setFieldValue('os_image', os[0].key);
        }
        if (envs.length && !envs.find((e) => e.key === form.values.environment)) {
          form.setFieldValue('environment', envs[0].key);
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedOs = useMemo(
    () => osImages.find((o) => o.key === form.values.os_image),
    [osImages, form.values.os_image],
  );
  const showSkipDnf = isAlmaOs(form.values.os_image, selectedOs?.label);

  useEffect(() => {
    if (!showSkipDnf && form.values.skip_dnf_groups) {
      form.setFieldValue('skip_dnf_groups', false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showSkipDnf]);

  const handleSubmit = form.onSubmit(async (values) => {
    setError(null);
    setSubmitting(true);
    try {
      const payload: BuildRequest = {
        ...values,
        hostname: values.hostname.trim(),
        ip: values.ip.trim(),
        gateway: values.gateway?.trim() || undefined,
        skip_dnf_groups: showSkipDnf ? !!values.skip_dnf_groups : false,
      };
      const job = await builds.submit(payload);
      notifications.show({
        title: 'Build queued',
        message: `${payload.hostname} is in the queue`,
        color: 'green',
        icon: <IconCheck size={16} />,
      });
      navigate(`/jobs/${job.id}`);
    } catch (err: any) {
      setError(err.message || 'Failed to submit build');
    } finally {
      setSubmitting(false);
    }
  });

  if (loading) {
    return <Center style={{ minHeight: 300 }}><Loader /></Center>;
  }

  const canSubmit = form.isValid() && !submitting;

  return (
    <Stack gap="lg" maw={640}>
      <div>
        <Title order={2} style={{ letterSpacing: '-0.02em' }}>Build a VM</Title>
        <Text size="sm" c="dimmed" mt={4}>Pick your options and submit \u2014 the cluster handles placement.</Text>
      </div>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} title="Error">{error}</Alert>
      )}

      <Card shadow="sm" padding="lg" radius="md" withBorder>
        <form onSubmit={handleSubmit}>
          <Stack gap="md">
            <Group grow>
              <TextInput label="Hostname" placeholder="web-01" required {...form.getInputProps('hostname')} />
              <TextInput label="IP Address" placeholder="10.0.1.50" required {...form.getInputProps('ip')} />
            </Group>

            <Group grow>
              <Select
                label="Operating System"
                data={osImages.map((o) => ({ value: o.key, label: o.label }))}
                required
                {...form.getInputProps('os_image')}
              />
              <Select
                label="Environment"
                data={environments.map((e) => ({ value: e.key, label: e.label }))}
                required
                {...form.getInputProps('environment')}
              />
            </Group>

            <Text size="xs" c="dimmed">
              Placement is automatic \u2014 dev goes to the dev datastore cluster, prod to prod. You never pick a LUN.
            </Text>

            <Divider label="Resources" labelPosition="center" />

            <Group grow>
              <NumberInput label="CPUs" min={1} max={64} {...form.getInputProps('cpus')} />
              <NumberInput label="Memory (GB)" min={1} max={512} {...form.getInputProps('memory_gb')} />
              <NumberInput label="Disk (GB)" min={10} max={4096} {...form.getInputProps('disk_gb')} />
            </Group>

            <Divider label="Network" labelPosition="center" />

            <Group grow>
              <TextInput label="Gateway" placeholder="10.0.1.1" {...form.getInputProps('gateway')} />
              <TextInput label="Prefix" placeholder="24" {...form.getInputProps('prefix')} />
            </Group>

            <TagsInput label="DNS Servers" placeholder="Add DNS servers" {...form.getInputProps('dns')} />

            <TextInput label="Location tag" placeholder="optional" {...form.getInputProps('location')} />

            {showSkipDnf && (
              <Switch
                label="Skip DNF groups (faster AlmaLinux builds)"
                {...form.getInputProps('skip_dnf_groups', { type: 'checkbox' })}
              />
            )}

            <Group justify="flex-end" mt="sm">
              <Button type="submit" loading={submitting} disabled={!canSubmit} leftSection={<IconRocket size={18} />} size="md">
                Build VM
              </Button>
            </Group>
          </Stack>
        </form>
      </Card>
    </Stack>
  );
}
