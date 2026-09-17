import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Stack, Title, Text, Table, Badge, Group, ActionIcon, Tooltip, Center, Loader, Alert, Button,
  ScrollArea, Anchor, Card, Box,
} from '@mantine/core';
import { IconRefresh, IconAlertCircle, IconEye, IconRocket } from '@tabler/icons-react';
import { builds, type BuildJob } from '../services/api';
import { Link } from 'react-router';
import { useAuth } from '../hooks/AuthContext';

const STATUS_COLOR: Record<string, string> = {
  queued: 'gray',
  running: 'blue',
  succeeded: 'green',
  failed: 'red',
};

function StatusBadge({ status }: { status: string }) {
  return <Badge color={STATUS_COLOR[status] || 'gray'} variant="light">{status}</Badge>;
}

function JobCard({ job }: { job: BuildJob }) {
  return (
    <Card withBorder padding="md" radius="md" component={Link} to={`/jobs/${job.id}`} style={{ textDecoration: 'none', color: 'inherit' }} aria-label={`View job for ${job.request.hostname}`}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Box style={{ minWidth: 0 }}>
          <Text fw={600} truncate>{job.request.hostname}</Text>
          <Text size="sm" c="dimmed">{job.request.os_image} · {job.request.environment}</Text>
          <Text size="xs" c="dimmed" mt={4}>{new Date(job.created_at).toLocaleString()}</Text>
        </Box>
        <StatusBadge status={job.status} />
      </Group>
    </Card>
  );
}

export function JobsPage() {
  const { user } = useAuth();
  const [jobs, setJobs] = useState<BuildJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasRows = useRef(false);

  const load = useCallback((opts?: { background?: boolean }) => {
    const background = opts?.background && hasRows.current;
    if (background) setRefreshing(true);
    else setLoading(true);
    builds.list()
      .then((rows) => {
        setJobs(rows);
        hasRows.current = rows.length > 0;
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => {
        setLoading(false);
        setRefreshing(false);
      });
  }, []);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh while any job is still running — do not blank the table.
  useEffect(() => {
    const hasRunning = jobs.some((j) => j.status === 'running' || j.status === 'queued');
    if (!hasRunning) return;
    const id = setInterval(() => load({ background: true }), 5000);
    return () => clearInterval(id);
  }, [jobs, load]);

  const canBuild = user?.role === 'admin' || user?.role === 'builder';

  if (loading && jobs.length === 0) {
    return <Center style={{ minHeight: 300 }}><Loader /></Center>;
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2} style={{ letterSpacing: '-0.02em' }}>My Builds</Title>
          <Text size="sm" c="dimmed" mt={4}>{jobs.length} job{jobs.length === 1 ? '' : 's'}</Text>
        </div>
        <Tooltip label="Refresh">
          <ActionIcon
            variant="subtle"
            color="gray"
            onClick={() => load({ background: jobs.length > 0 })}
            aria-label="Refresh jobs"
            loading={refreshing || (loading && jobs.length > 0)}
          >
            <IconRefresh size={18} />
          </ActionIcon>
        </Tooltip>
      </Group>

      {error && <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>}

      {jobs.length === 0 ? (
        <Stack align="center" py="xl" gap="md">
          <Text c="dimmed" ta="center">No builds yet.</Text>
          {canBuild && (
            <Button component={Link} to="/build" leftSection={<IconRocket size={16} />}>
              Build a VM
            </Button>
          )}
        </Stack>
      ) : (
        <>
          <Stack gap="sm" hiddenFrom="sm">
            {jobs.map((j) => (
              <JobCard key={j.id} job={j} />
            ))}
          </Stack>

          <ScrollArea visibleFrom="sm">
            <Table striped highlightOnHover miw={640}>
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
                  <Table.Tr key={j.id}>
                    <Table.Td>
                      <Anchor component={Link} to={`/jobs/${j.id}`} fw={500} aria-label={`View job for ${j.request.hostname}`}>
                        {j.request.hostname}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>{j.request.os_image}</Table.Td>
                    <Table.Td>{j.request.environment}</Table.Td>
                    <Table.Td><StatusBadge status={j.status} /></Table.Td>
                    <Table.Td><Text size="sm" c="dimmed">{new Date(j.created_at).toLocaleString()}</Text></Table.Td>
                    <Table.Td>
                      <ActionIcon
                        component={Link}
                        to={`/jobs/${j.id}`}
                        variant="subtle"
                        color="gray"
                        aria-label={`Open job ${j.request.hostname}`}
                      >
                        <IconEye size={16} />
                      </ActionIcon>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        </>
      )}
    </Stack>
  );
}
