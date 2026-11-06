import { useEffect, useState } from 'react';
import {
  ActionIcon, Alert, Badge, Box, Button, Card, Center, Checkbox, Group, Loader, Menu, Modal,
  NumberInput, ScrollArea, Stack, Table, Text, TextInput, Title, Tooltip,
} from '@mantine/core';
import {
  IconAlertCircle, IconCalendarTime, IconCamera, IconDots, IconPlayerPlay, IconPlayerStop,
  IconRecycle, IconRefresh, IconResize, IconRotate, IconRotateClockwise2, IconTrash,
} from '@tabler/icons-react';
import {
  vms, type InventoryVm, type ReclaimReport, type VmLease, type VmResize, type VmSnapshot,
} from '../services/api';
import { useAuth } from '../hooks/AuthContext';

function powerColor(state: string) {
  if (state === 'poweredOn') return 'green';
  if (state === 'poweredOff') return 'gray';
  return 'yellow';
}

function fmtDate(value?: string | null) {
  if (!value) return '\u2014';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function isExpired(value?: string | null) {
  return !!value && new Date(value).getTime() <= Date.now();
}

interface Confirmation {
  title: string;
  body: string;
  label: string;
  color: string;
  name: string;
  run: () => Promise<unknown>;
}

function VmActions({
  vm, busy, canDestroy, onPowerOn, onPowerOff, onReboot, onReset, onSnapshots, onResize, onLease, onDestroy,
}: {
  vm: InventoryVm;
  busy: string | null;
  canDestroy: boolean;
  onPowerOn: () => void;
  onPowerOff: () => void;
  onReboot: () => void;
  onReset: () => void;
  onSnapshots: () => void;
  onResize: () => void;
  onLease: () => void;
  onDestroy: () => void;
}) {
  const loading = busy === vm.name;
  if (vm.orphan) {
    return (
      <Group gap={4} wrap="nowrap">
        <Tooltip label="Destroy leftover VM from a failed or cancelled build">
          <Button size="compact-xs" variant="light" color="red" loading={loading}
            leftSection={<IconTrash size={14} />} onClick={onDestroy}>
            Clean up
          </Button>
        </Tooltip>
      </Group>
    );
  }
  const on = vm.power_state === 'poweredOn';
  return (
    <Group gap={4} wrap="nowrap">
      <Tooltip label="Power on">
        <ActionIcon size="sm" variant="subtle" color="green" loading={loading} disabled={on}
          aria-label={`Power on ${vm.name}`} onClick={onPowerOn}>
          <IconPlayerPlay size={16} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label="Power off">
        <ActionIcon size="sm" variant="subtle" color="orange" loading={loading} disabled={!on}
          aria-label={`Power off ${vm.name}`} onClick={onPowerOff}>
          <IconPlayerStop size={16} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label="Reboot guest">
        <ActionIcon size="sm" variant="subtle" color="blue" loading={loading} disabled={!on}
          aria-label={`Reboot ${vm.name}`} onClick={onReboot}>
          <IconRotate size={16} />
        </ActionIcon>
      </Tooltip>
      <Menu position="bottom-end" withinPortal>
        <Menu.Target>
          <ActionIcon size="sm" variant="subtle" color="gray" aria-label={`More actions for ${vm.name}`}>
            <IconDots size={16} />
          </ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item leftSection={<IconRotateClockwise2 size={14} />} disabled={!on} onClick={onReset}>
            Hard reset
          </Menu.Item>
          <Menu.Item leftSection={<IconCamera size={14} />} onClick={onSnapshots}>Snapshots</Menu.Item>
          <Menu.Item leftSection={<IconResize size={14} />} onClick={onResize}>Resize</Menu.Item>
          {vm.managed && (
            <Menu.Item leftSection={<IconCalendarTime size={14} />} onClick={onLease}>Lease</Menu.Item>
          )}
          {canDestroy && (
            <>
              <Menu.Divider />
              <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={onDestroy}>
                Destroy
              </Menu.Item>
            </>
          )}
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}

function SnapshotsModal({
  vmName, onClose, onConfirm,
}: {
  vmName: string | null;
  onClose: () => void;
  onConfirm: (c: Confirmation) => void;
}) {
  const [rows, setRows] = useState<VmSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [snapName, setSnapName] = useState('ovbuilder');
  const [memory, setMemory] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = () => {
    if (!vmName) return;
    setLoading(true);
    setError(null);
    vms.snapshots(vmName).then(setRows).catch((e) => setError(e.message)).finally(() => setLoading(false));
  };

  useEffect(() => {
    setRows([]);
    setSnapName('ovbuilder');
    setMemory(false);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vmName]);

  const create = async () => {
    if (!vmName) return;
    setCreating(true);
    setError(null);
    try { await vms.snapshot(vmName, snapName.trim(), '', memory); load(); }
    catch (e: any) { setError(e.message || String(e)); }
    finally { setCreating(false); }
  };

  const withReload = (fn: () => Promise<unknown>) => async () => { await fn(); load(); };

  return (
    <Modal opened={!!vmName} onClose={onClose} title={`Snapshots of ${vmName || ''}`} size="lg">
      <Stack>
        {error && <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>}
        <Group align="flex-end">
          <TextInput label="New snapshot" value={snapName} style={{ flex: 1 }}
            onChange={(e) => setSnapName(e.currentTarget.value)} />
          <Button disabled={!snapName.trim()} loading={creating} onClick={create}>Create</Button>
        </Group>
        <Checkbox label="Include memory (running VMs only; slower, larger)" checked={memory}
          onChange={(e) => setMemory(e.currentTarget.checked)} />
        {loading ? (
          <Center py="md"><Loader size="sm" /></Center>
        ) : rows.length === 0 ? (
          <Text size="sm" c="dimmed" ta="center" py="md">No snapshots.</Text>
        ) : (
          <ScrollArea>
            <Table striped miw={500}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Created</Table.Th>
                  <Table.Th></Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((s) => (
                  <Table.Tr key={s.id}>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm" fw={500}>{s.name}</Text>
                        {s.current && <Badge size="xs" variant="light">current</Badge>}
                      </Group>
                    </Table.Td>
                    <Table.Td><Text size="sm">{fmtDate(s.created)}</Text></Table.Td>
                    <Table.Td>
                      <Group gap={4} justify="flex-end" wrap="nowrap">
                        <Button size="compact-xs" variant="light" onClick={() => vmName && onConfirm({
                          title: 'Restore snapshot',
                          body: `Restore ${vmName} to "${s.name}"? Changes made since that snapshot are lost.`,
                          label: 'Restore',
                          color: 'blue',
                          name: vmName,
                          run: withReload(() => vms.revertSnapshot(vmName, s.id)),
                        })}>Restore</Button>
                        <Button size="compact-xs" variant="subtle" color="red" onClick={() => vmName && onConfirm({
                          title: 'Delete snapshot',
                          body: `Delete snapshot "${s.name}" of ${vmName}? Its changes are merged into the VM's disks.`,
                          label: 'Delete',
                          color: 'red',
                          name: vmName,
                          run: withReload(() => vms.deleteSnapshot(vmName, s.id)),
                        })}>Delete</Button>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        )}
      </Stack>
    </Modal>
  );
}

function ResizeModal({
  vm, onClose, onSubmit,
}: {
  vm: InventoryVm | null;
  onClose: () => void;
  onSubmit: (name: string, body: VmResize) => Promise<void>;
}) {
  const currentMemGb = vm?.memory_mb ? Math.round(vm.memory_mb / 1024) : undefined;
  const [cpus, setCpus] = useState<number | string>('');
  const [mem, setMem] = useState<number | string>('');
  const [disk, setDisk] = useState<number | string>('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setCpus(vm?.cpus ?? '');
    setMem(currentMemGb ?? '');
    setDisk('');
  }, [vm, currentMemGb]);

  const body: VmResize = {};
  if (typeof cpus === 'number' && cpus !== vm?.cpus) body.cpus = cpus;
  if (typeof mem === 'number' && mem !== currentMemGb) body.memory_gb = mem;
  if (typeof disk === 'number') body.disk_gb = disk;
  const dirty = Object.keys(body).length > 0;

  return (
    <Modal opened={!!vm} onClose={onClose} title={`Resize ${vm?.name || ''}`} centered>
      <Stack>
        <NumberInput label="vCPUs" min={1} max={128} value={cpus} onChange={setCpus} />
        <NumberInput label="Memory (GB)" min={1} max={4096} value={mem} onChange={setMem} />
        <NumberInput label="Grow first disk to (GB)" min={1}
          description={vm?.disk_gb ? `Currently ${vm.disk_gb} GB across all disks. Disks can only grow.` : 'Disks can only grow.'}
          placeholder="Leave empty to keep" value={disk} onChange={setDisk} />
        <Text size="xs" c="dimmed">
          Running VMs need CPU/memory hot-add enabled; otherwise power off first. Growing a disk
          does not extend the guest filesystem, and disks with snapshots cannot grow.
        </Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button disabled={!dirty} loading={saving} onClick={async () => {
            if (!vm) return;
            setSaving(true);
            try { await onSubmit(vm.name, body); } finally { setSaving(false); }
          }}>Apply</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function LeaseModal({
  vmName, isAdmin, onClose, onSaved,
}: {
  vmName: string | null;
  isAdmin: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [lease, setLease] = useState<VmLease | null>(null);
  const [days, setDays] = useState<number | string>(30);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setLease(null);
    setError(null);
    if (vmName) vms.lease(vmName).then(setLease).catch((e) => setError(e.message));
  }, [vmName]);

  const save = async (expiresAt: string | null) => {
    if (!vmName) return;
    setSaving(true);
    setError(null);
    try { setLease(await vms.setLease(vmName, expiresAt)); onSaved(); }
    catch (e: any) { setError(e.message || String(e)); }
    finally { setSaving(false); }
  };

  const maxDays = lease?.max_days ?? 90;
  return (
    <Modal opened={!!vmName} onClose={onClose} title={`Lease for ${vmName || ''}`} centered>
      <Stack>
        {error && <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>}
        <Text size="sm">
          Expires: <b>{lease ? (lease.expires_at ? fmtDate(lease.expires_at) : 'never') : '\u2026'}</b>
          {lease?.explicit === false && lease.expires_at ? ' (default policy)' : ''}
        </Text>
        <Text size="xs" c="dimmed">
          After expiry the reclaim job may power off or destroy this VM, depending on site policy.
        </Text>
        <Group align="flex-end">
          <NumberInput label="Expire in (days from now)" min={1} max={isAdmin ? undefined : maxDays}
            value={days} onChange={setDays} style={{ flex: 1 }} />
          <Button loading={saving} disabled={typeof days !== 'number'} onClick={() => {
            if (typeof days !== 'number') return;
            save(new Date(Date.now() + days * 86400000).toISOString());
          }}>Set</Button>
        </Group>
        {isAdmin && (
          <Button variant="subtle" loading={saving} onClick={() => save(null)}>Never expire</Button>
        )}
      </Stack>
    </Modal>
  );
}

function ReclaimModal({ opened, onClose, onDone }: { opened: boolean; onClose: () => void; onDone: () => void }) {
  const [report, setReport] = useState<ReclaimReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = (fn: () => Promise<ReclaimReport>) => {
    setLoading(true);
    setError(null);
    fn().then(setReport).catch((e) => setError(e.message)).finally(() => setLoading(false));
  };

  useEffect(() => {
    if (opened) { setReport(null); load(vms.reclaimPreview); }
  }, [opened]);

  const actionable = report?.candidates.filter((c) => c.action !== 'none') ?? [];
  return (
    <Modal opened={opened} onClose={onClose} title="Reclaim expired and idle VMs" size="lg">
      <Stack>
        {error && <Alert color="red" icon={<IconAlertCircle size={16} />}>{error}</Alert>}
        {report && (
          <Text size="sm" c="dimmed">
            Policy: lease {report.policy.lease_days || 'off'} day(s), idle after {report.policy.idle_days || 'off'} day(s),
            action <b>{report.policy.action}</b>. Scheduled reclaim is {report.enabled ? 'enabled' : 'disabled'}.
            {report.dry_run ? ' Preview only; nothing has changed.' : ''}
          </Text>
        )}
        {loading ? (
          <Center py="md"><Loader size="sm" /></Center>
        ) : report && report.candidates.length === 0 ? (
          <Text size="sm" c="dimmed" ta="center" py="md">Nothing to reclaim.</Text>
        ) : report && (
          <ScrollArea>
            <Table striped miw={500}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>VM</Table.Th><Table.Th>Owner</Table.Th><Table.Th>Reason</Table.Th><Table.Th>Action</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {report.candidates.map((c) => (
                  <Table.Tr key={c.name}>
                    <Table.Td>{c.name}</Table.Td>
                    <Table.Td>{c.owner}</Table.Td>
                    <Table.Td>{c.reason === 'expired' ? `expired ${fmtDate(c.expires_at)}` : `off since ${fmtDate(c.powered_off_since)}`}</Table.Td>
                    <Table.Td>
                      {c.error ? <Text size="sm" c="red">{c.error}</Text>
                        : <Badge variant="light" color={c.action === 'destroy' ? 'red' : c.action === 'none' ? 'gray' : 'orange'}>
                            {c.done ? `${c.action} done` : c.action}
                          </Badge>}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Close</Button>
          <Button color="red" disabled={!report?.dry_run || actionable.length === 0} loading={loading}
            onClick={() => load(async () => { const r = await vms.reclaim(); onDone(); return r; })}>
            Reclaim {actionable.length || ''} now
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export function VMsPage() {
  const { user } = useAuth();
  const [rows, setRows] = useState<InventoryVm[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [snapFor, setSnapFor] = useState<string | null>(null);
  const [resizeFor, setResizeFor] = useState<InventoryVm | null>(null);
  const [leaseFor, setLeaseFor] = useState<string | null>(null);
  const [destroyFor, setDestroyFor] = useState<InventoryVm | null>(null);
  const [destroyTyped, setDestroyTyped] = useState('');
  const [reclaimOpen, setReclaimOpen] = useState(false);
  const [confirm, setConfirm] = useState<Confirmation | null>(null);
  const isAdmin = user?.role === 'admin';

  const load = () => {
    setLoading(true);
    vms.list().then(setRows).catch((e) => setError(e.message)).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    setError(null);
    setNotice(null);
    try {
      const res = (await fn()) as Record<string, unknown> | undefined;
      if (res && typeof res.note === 'string') setNotice(res.note);
      await load();
      return true;
    } catch (e: any) {
      setError(e.message || String(e));
      return false;
    } finally { setBusy(null); }
  };

  const runConfirmed = async () => {
    if (!confirm) return;
    const { name, run } = confirm;
    setConfirm(null);
    await act(name, run);
  };

  if (loading && rows.length === 0) {
    return <Center style={{ minHeight: 300 }}><Loader /></Center>;
  }

  const canDestroy = (vm: InventoryVm) => isAdmin || vm.owner === user?.username;

  const actionsFor = (vm: InventoryVm) => (
    <VmActions
      vm={vm}
      busy={busy}
      canDestroy={canDestroy(vm)}
      onPowerOn={() => act(vm.name, () => vms.powerOn(vm.name))}
      onPowerOff={() => setConfirm({
        title: 'Power off VM', body: `Power off ${vm.name}? Running workloads will stop.`,
        label: 'Power off', color: 'orange', name: vm.name, run: () => vms.powerOff(vm.name),
      })}
      onReboot={() => setConfirm({
        title: 'Reboot VM', body: `Reboot ${vm.name}? The guest will restart.`,
        label: 'Reboot', color: 'blue', name: vm.name, run: () => vms.reboot(vm.name),
      })}
      onReset={() => setConfirm({
        title: 'Hard reset VM', body: `Hard reset ${vm.name}? This is a power cycle; unsaved guest data is lost.`,
        label: 'Reset', color: 'red', name: vm.name, run: () => vms.reset(vm.name),
      })}
      onSnapshots={() => setSnapFor(vm.name)}
      onResize={() => setResizeFor(vm)}
      onLease={() => setLeaseFor(vm.name)}
      onDestroy={() => { setDestroyFor(vm); setDestroyTyped(''); }}
    />
  );

  const expiryBadge = (vm: InventoryVm) => vm.expires_at ? (
    <Badge size="sm" variant="light" color={isExpired(vm.expires_at) ? 'red' : 'gray'}>
      {isExpired(vm.expires_at) ? 'expired' : `until ${new Date(vm.expires_at).toLocaleDateString()}`}
    </Badge>
  ) : null;

  const statusBadges = (vm: InventoryVm) => (
    <Group gap={4} wrap="nowrap">
      <Badge color={powerColor(vm.power_state)} variant="light">{vm.power_state}</Badge>
      {vm.orphan && <Badge color="red" variant="outline">partial build</Badge>}
    </Group>
  );

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2} style={{ letterSpacing: '-0.02em' }}>Virtual machines</Title>
          <Text size="sm" c="dimmed" mt={4}>{rows.length} VM{rows.length === 1 ? '' : 's'} in inventory</Text>
        </div>
        <Group gap="xs">
          {isAdmin && (
            <Button size="xs" variant="light" leftSection={<IconRecycle size={14} />} onClick={() => setReclaimOpen(true)}>
              Reclaim
            </Button>
          )}
          <Tooltip label="Refresh">
            <ActionIcon variant="subtle" color="gray" onClick={load} aria-label="Refresh virtual machines" loading={loading}>
              <IconRefresh size={18} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      {error && <Alert color="red" icon={<IconAlertCircle size={16} />} withCloseButton onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert color="blue" withCloseButton onClose={() => setNotice(null)}>{notice}</Alert>}

      {rows.length === 0 ? (
        <Text c="dimmed" ta="center" py="xl">No VMs visible to this vCenter session.</Text>
      ) : (
        <>
          <Stack gap="sm" hiddenFrom="sm">
            {rows.map((vm) => (
              <Card key={vm.uuid || vm.name} withBorder padding="md" radius="md">
                <Group justify="space-between" align="flex-start" wrap="nowrap" mb="sm">
                  <Box style={{ minWidth: 0 }}>
                    <Text fw={600} truncate>{vm.name}</Text>
                    <Text size="sm" c="dimmed" lineClamp={1}>{vm.guest_os || '\u2014'}</Text>
                    <Text size="xs" c="dimmed" mt={4}>
                      {vm.ip || '\u2014'} · {vm.cpus ?? '\u2014'} vCPU / {vm.memory_mb ? `${Math.round(vm.memory_mb / 1024)} GB` : '\u2014'}
                      {vm.owner ? ` · ${vm.owner}` : ''}
                    </Text>
                    <Box mt={4}>{expiryBadge(vm)}</Box>
                  </Box>
                  {statusBadges(vm)}
                </Group>
                {actionsFor(vm)}
              </Card>
            ))}
          </Stack>

          <ScrollArea visibleFrom="sm">
            <Table striped highlightOnHover miw={860}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Power</Table.Th>
                  <Table.Th>IP</Table.Th>
                  <Table.Th>OS</Table.Th>
                  <Table.Th>vCPU / RAM / Disk</Table.Th>
                  <Table.Th>Owner</Table.Th>
                  <Table.Th>Lease</Table.Th>
                  <Table.Th></Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((vm) => (
                  <Table.Tr key={vm.uuid || vm.name}>
                    <Table.Td><Text fw={500}>{vm.name}</Text></Table.Td>
                    <Table.Td>{statusBadges(vm)}</Table.Td>
                    <Table.Td>{vm.ip || '\u2014'}</Table.Td>
                    <Table.Td><Text size="sm" lineClamp={1}>{vm.guest_os || '\u2014'}</Text></Table.Td>
                    <Table.Td>
                      <Text size="sm">
                        {vm.cpus ?? '\u2014'} / {vm.memory_mb ? `${Math.round(vm.memory_mb / 1024)} GB` : '\u2014'} / {vm.disk_gb ? `${vm.disk_gb} GB` : '\u2014'}
                      </Text>
                    </Table.Td>
                    <Table.Td><Text size="sm">{vm.owner || '\u2014'}</Text></Table.Td>
                    <Table.Td>{expiryBadge(vm) || <Text size="sm" c="dimmed">{'\u2014'}</Text>}</Table.Td>
                    <Table.Td>{actionsFor(vm)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        </>
      )}

      <SnapshotsModal vmName={snapFor} onClose={() => setSnapFor(null)} onConfirm={setConfirm} />

      <ResizeModal vm={resizeFor} onClose={() => setResizeFor(null)} onSubmit={async (name, body) => {
        if (await act(name, () => vms.resize(name, body))) setResizeFor(null);
      }} />

      <LeaseModal vmName={leaseFor} isAdmin={isAdmin} onClose={() => setLeaseFor(null)} onSaved={load} />

      {isAdmin && <ReclaimModal opened={reclaimOpen} onClose={() => setReclaimOpen(false)} onDone={load} />}

      <Modal opened={!!destroyFor} onClose={() => setDestroyFor(null)} title="Destroy VM" centered>
        <Stack>
          <Text size="sm">
            Permanently delete <b>{destroyFor?.name}</b> and its disks? Its Terraform state is retired.
            This cannot be undone.
          </Text>
          <TextInput label="Type the VM name to confirm" value={destroyTyped}
            onChange={(e) => setDestroyTyped(e.currentTarget.value)} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setDestroyFor(null)}>Cancel</Button>
            <Button color="red" disabled={destroyTyped !== destroyFor?.name} loading={busy === destroyFor?.name}
              onClick={async () => {
                if (!destroyFor) return;
                const name = destroyFor.name;
                setDestroyFor(null);
                await act(name, () => vms.destroy(name));
              }}>
              Destroy
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={!!confirm} onClose={() => setConfirm(null)} title={confirm?.title} centered>
        <Stack>
          <Text size="sm">{confirm?.body}</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirm(null)}>Cancel</Button>
            <Button color={confirm?.color || 'red'} loading={busy === confirm?.name} onClick={runConfirmed}>
              {confirm?.label || 'Confirm'}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
