import { useState, useEffect, useRef } from 'react';
import {
  Stack, Title, Text, Group, Badge, Card, Code, ScrollArea, Alert, Button, Loader, Center,
} from '@mantine/core';
import { IconArrowLeft, IconAlertCircle, IconCheck, IconX } from '@tabler/icons-react';
import { builds, type BuildJob } from '../services/api';
import { useParams, useNavigate } from 'react-router';

const STATUS_COLOR: Record<string, string> = {
  queued: 'gray',
  running: 'blue',
  succeeded: 'green',
  failed: 'red',
  cancelling: 'orange',
  cancelled: 'gray',
};

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<BuildJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  const load = () => {
    if (!id) return;
    builds.get(id)
      .then(setJob)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [id]);

  useEffect(() => {
    if (!job || !['running', 'queued', 'cancelling'].includes(job.status)) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [job?.status]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [job?.log_tail]);

  const onCancel = async () => {
    if (!id || !job) return;
    if (!window.confirm(`Cancel the build for ${job.request.hostname}?`)) return;
    setCancelling(true);
    setError(null);
    try {
      const updated = await builds.cancel(id);
      setJob(updated);
    } catch (e: any) {
      setError(e.message || 'Cancel failed');
    } finally {
      setCancelling(false);
    }
  };

  if (loading && !job) return <Center style={{ minHeight: 300 }}><Loader color="ovred" /></Center>;
  if (error && !job) return <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>;
  if (!job) return <Text c="dimmed">Job not found.</Text>;

  const r = job.request;
  const canCancel = job.status === 'queued' || job.status === 'running';
  return (
    <Stack gap="lg" maw={800}>
      <Group>
        <Button variant="subtle" color="gray" leftSection={<IconArrowLeft size={16} />} onClick={() => navigate('/jobs')}>
          Back
        </Button>
        <Title order={2} style={{ letterSpacing: '-0.02em' }}>{r.hostname}</Title>
        <Badge color={STATUS_COLOR[job.status] || 'gray'} variant="light" size="lg">{job.status}</Badge>
        {canCancel && (
          <Button color="red" variant="light" loading={cancelling} onClick={onCancel}>
            Cancel build
          </Button>
        )}
      </Group>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>
      )}

      <Group gap="xl">
        <Text size="sm"><Text span c="dimmed">OS:</Text> {r.os_image}</Text>
        <Text size="sm"><Text span c="dimmed">Env:</Text> {r.environment}</Text>
        <Text size="sm"><Text span c="dimmed">IP:</Text> {r.ip}</Text>
        <Text size="sm"><Text span c="dimmed">By:</Text> {job.requested_by}</Text>
        {job.vm_ip && <Text size="sm"><Text span c="dimmed">VM IP:</Text> {job.vm_ip}</Text>}
      </Group>

      {job.status === 'cancelled' && (
        <Alert color="gray" icon={<IconX size={16} />} title="Build cancelled">{job.error || 'Cancelled by user.'}</Alert>
      )}
      {job.status === 'cancelling' && (
        <Alert color="orange" icon={<IconAlertCircle size={16} />} title="Cancelling">Waiting for the worker to stop.</Alert>
      )}
      {job.error && job.status === 'failed' && (
        <Alert color="red" icon={<IconX size={16} />} title="Build failed">{job.error}</Alert>
      )}
      {job.status === 'succeeded' && (
        <Alert color="green" icon={<IconCheck size={16} />} title="Build succeeded">
          {job.vm_ip ? `VM is up at ${job.vm_ip}` : 'VM provisioned successfully.'}
        </Alert>
      )}

      <Card shadow="sm" padding={0} radius="md" withBorder>
        <Group justify="space-between" px="md" py="sm" style={{ borderBottom: '1px solid var(--ov-line)' }}>
          <Text fw={600} size="sm">Build log</Text>
          <Text size="xs" c="dimmed">{job.log_tail ? job.log_tail.split('\n').length : 0} lines</Text>
        </Group>
        <ScrollArea h={420} viewportRef={logRef} p="md">
          <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'transparent' }}>
            {job.log_tail || 'No output yet…'}
          </Code>
        </ScrollArea>
      </Card>
    </Stack>
  );
}
