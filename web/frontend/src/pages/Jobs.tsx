import { useState, useEffect } from 'react';
import { Stack, Title, Text, Table, Badge, Group, ActionIcon, Tooltip, Center, Loader, Alert } from '@mantine/core';
import { IconRefresh, IconAlertCircle, IconEye } from '@tabler/icons-react';
import { builds, type BuildJob } from '../services/api';
import { useNavigate } from 'react-router';

const STATUS_COLOR: Record<string, string> = {
  queued: 'gray',
  running: 'blue',
  succeeded: 'green',
  failed: 'red',
};

function StatusBadge({ status }: { status: string }) {
  return <Badge color={STATUS_COLOR[status] || 'gray'} variant="light">{status}</Badge>;
}

export function JobsPage() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<BuildJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    builds.list()
      .then(setJobs)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  // Auto-refresh while any job is still running.
  useEffect(() => {
    const hasRunning = jobs.some((j) => j.status === 'running' || j.status === 'queued');
    if (!hasRunning) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [jobs]);

  if (loading && jobs.length === 0) {
    return <Center style={{ minHeight: 300 }}><Loader color="ovred" /></Center>;
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2} style={{ letterSpacing: '-0.02em' }}>My Builds</Title>
          <Text size="sm" c="dimmed" mt={4}>{jobs.length} job{jobs.length === 1 ? '' : 's'}</Text>
        </div>
        <Tooltip label="Refresh"><ActionIcon variant="subtle" color="gray" onClick={load}><IconRefresh size={18} /></ActionIcon></Tooltip>
      </Group>

      {error && <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>}

      {jobs.length === 0 ? (
        <Text c="dimmed" ta="center" py="xl">No builds yet. Head to Build a VM to get started.</Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Hostname</Table.Th>
              <Table.Th>OS</Table.Th>
              <Table.Th>Env</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Requested</Table.Th>
              <Table.Th></Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {jobs.map((j) => (
              <Table.Tr key={j.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/jobs/${j.id}`)}>
                <Table.Td><Text fw={500}>{j.request.hostname}</Text></Table.Td>
                <Table.Td>{j.request.os_image}</Table.Td>
                <Table.Td>{j.request.environment}</Table.Td>
                <Table.Td><StatusBadge status={j.status} /></Table.Td>
                <Table.Td><Text size="sm" c="dimmed">{new Date(j.created_at).toLocaleString()}</Text></Table.Td>
                <Table.Td><IconEye size={16} color="var(--mantine-color-dimmed)" /></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
