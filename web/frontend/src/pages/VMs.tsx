import { useEffect, useState } from 'react';
import {
  ActionIcon, Alert, Badge, Button, Center, Group, Loader, Modal, Stack,
  Table, Text, TextInput, Title, Tooltip,
} from '@mantine/core';
import {
  IconAlertCircle, IconPlayerPlay, IconPlayerStop, IconRefresh,
  IconRotate, IconCamera, IconTrash,
} from '@tabler/icons-react';
import { vms, type InventoryVm } from '../services/api';
import { useAuth } from '../hooks/AuthContext';

function powerColor(state: string) {
  if (state.includes('On') || state === 'poweredOn') return 'green';
  if (state.includes('Off') || state === 'poweredOff') return 'gray';
  return 'yellow';
}

export function VMsPage() {
  const { user } = useAuth();
  const [rows, setRows] = useState<InventoryVm[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [snapFor, setSnapFor] = useState<string | null>(null);
  const [snapName, setSnapName] = useState('ovbuilder');
  const isAdmin = user?.role === 'admin';

  const load = () => {
    setLoading(true);
    vms.list()
      .then(setRows)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setBusy(null);
    }
  };

  if (loading && rows.length === 0) {
    return <Center style={{ minHeight: 300 }}><Loader color="ovred" /></Center>;
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2} style={{ letterSpacing: '-0.02em' }}>Virtual machines</Title>
          <Text size="sm" c="dimmed" mt={4}>{rows.length} VM{rows.length === 1 ? '' : 's'} in inventory</Text>
        </div>
        <Tooltip label="Refresh">
          <ActionIcon variant="subtle" color="gray" onClick={load}><IconRefresh size={18} /></ActionIcon>
        </Tooltip>
      </Group>

      {error && <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>}

      {rows.length === 0 ? (
        <Text c="dimmed" ta="center" py="xl">No VMs visible to this vCenter session.</Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Power</Table.Th>
              <Table.Th>IP</Table.Th>
              <Table.Th>OS</Table.Th>
              <Table.Th>vCPU / RAM</Table.Th>
              <Table.Th></Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((vm) => (
              <Table.Tr key={vm.uuid || vm.name}>
                <Table.Td><Text fw={500}>{vm.name}</Text></Table.Td>
                <Table.Td><Badge color={powerColor(vm.power_state)} variant="light">{vm.power_state}</Badge></Table.Td>
                <Table.Td>{vm.ip || '—'}</Table.Td>
                <Table.Td><Text size="sm" lineClamp={1}>{vm.guest_os || '—'}</Text></Table.Td>
                <Table.Td>
                  <Text size="sm">{vm.cpus ?? '—'} / {vm.memory_mb ? `${Math.round(vm.memory_mb / 1024)} GB` : '—'}</Text>
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <Tooltip label="Power on">
                      <ActionIcon size="sm" variant="subtle" color="green" loading={busy === vm.name}
                        onClick={() => act(vm.name, () => vms.powerOn(vm.name))}>
                        <IconPlayerPlay size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Power off">
                      <ActionIcon size="sm" variant="subtle" color="orange" loading={busy === vm.name}
                        onClick={() => act(vm.name, () => vms.powerOff(vm.name))}>
                        <IconPlayerStop size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Reboot">
                      <ActionIcon size="sm" variant="subtle" color="blue" loading={busy === vm.name}
                        onClick={() => act(vm.name, () => vms.reboot(vm.name))}>
                        <IconRotate size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Snapshot">
                      <ActionIcon size="sm" variant="subtle" color="gray"
                        onClick={() => { setSnapFor(vm.name); setSnapName('ovbuilder'); }}>
                        <IconCamera size={16} />
                      </ActionIcon>
                    </Tooltip>
                    {isAdmin && (
                      <Tooltip label="Destroy">
                        <ActionIcon size="sm" variant="subtle" color="red" loading={busy === vm.name}
                          onClick={() => {
                            if (window.confirm(`Destroy ${vm.name}? This cannot be undone.`)) {
                              act(vm.name, () => vms.destroy(vm.name));
                            }
                          }}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal opened={!!snapFor} onClose={() => setSnapFor(null)} title={`Snapshot ${snapFor || ''}`}>
        <Stack>
          <TextInput label="Snapshot name" value={snapName} onChange={(e) => setSnapName(e.currentTarget.value)} />
          <Button color="ovred" disabled={!snapName.trim()} loading={busy === snapFor}
            onClick={() => {
              if (!snapFor) return;
              act(snapFor, () => vms.snapshot(snapFor, snapName.trim())).then(() => setSnapFor(null));
            }}>
            Create snapshot
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
