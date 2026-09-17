import { useState, useEffect, useRef } from 'react';
import {
  Stack, Title, Text, Group, Badge, Card, Code, ScrollArea, Alert, Button, Loader, Center,
  Modal, SimpleGrid, Box,
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

function fmt(ts?: string) {
  if (!ts) return '\u2014';
  try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<BuildJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
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
    setConfirmCancel(false);
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

  if (loading && !job) return <Center style={{ minHeight: 300 }}><Loader /></Center>;
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
        <Badge color={STATUS_COLOR[job.status] || 'gray'} variant="light" size="lg" aria-live="polite">{job.status}</Badge>
        {canCancel && (
          <Button color="red" variant="light" loading={cancelling} onClick={() => setConfirmCancel(true)}>
            Cancel build
          </Button>
        )}
      </Group>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>
      )}

      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
        <Box><Text size="xs" c="dimmed">OS</Text><Text size="sm">{r.os_image}</Text></Box>
        <Box><Text size="xs" c="dimmed">Environment</Text><Text size="sm">{r.environment}</Text></Box>
        <Box><Text size="xs" c="dimmed">IP</Text><Text size="sm">{r.ip}</Text></Box>
        <Box><Text size="xs" c="dimmed">Requested by</Text><Text size="sm">{job.requested_by}</Text></Box>
        {job.vm_ip && (<Box><Text size="xs" c="dimmed">VM IP</Text><Text size="sm">{job.vm_ip}</Text></Box>)}
        <Box><Text size="xs" c="dimmed">Created</Text><Text size="sm">{fmt(job.created_at)}</Text></Box>
        {job.started_at && (<Box><Text size="xs" c="dimmed">Started</Text><Text size="sm">{fmt(job.started_at)}</Text></Box>)}
        {job.finished_at && (<Box><Text size="xs" c="dimmed">Finished</Text><Text size="sm">{fmt(job.finished_at)}</Text></Box>)}
      </SimpleGrid>

      <div aria-live="polite">
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
      </div>

      <Card shadow="sm" padding={0} radius="md" withBorder>
        <Group justify="space-between" px="md" py="sm" style={{ borderBottom: '1px solid var(--ov-line)' }}>
          <Text fw={600} size="sm">Build log</Text>
          <Text size="xs" c="dimmed" aria-live="polite">{job.log_tail ? job.log_tail.split('\n').length : 0} lines</Text>
        </Group>
        <ScrollArea h={420} viewportRef={logRef} p="md">
          <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'transparent' }}>
            {job.log_tail || 'No output yet\u2026'}
          </Code>
        </ScrollArea>
      </Card>

      <Modal opened={confirmCancel} onClose={() => setConfirmCancel(false)} title="Cancel build" centered>
        <Stack>
          <Text size="sm">Cancel the build for <strong>{job.request.hostname}</strong>?</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirmCancel(false)}>Keep building</Button>
            <Button color="red" loading={cancelling} onClick={onCancel}>Cancel build</Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
