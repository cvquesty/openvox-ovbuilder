import { useEffect, useState, type FormEvent } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconCheck, IconUsers } from '@tabler/icons-react';
import { auth, type AppRole, type AuthUserAdmin } from '../services/api';

const ROLE_OPTIONS: { value: AppRole | ''; label: string }[] = [
  { value: '', label: 'LDAP mapping (no override)' },
  { value: 'admin', label: 'admin' },
  { value: 'builder', label: 'builder' },
  { value: 'viewer', label: 'viewer' },
];

function roleColor(role: string): string {
  if (role === 'admin') return 'red';
  if (role === 'builder') return 'blue';
  return 'gray';
}

function rank(role: string | null | undefined): number {
  if (role === 'admin') return 3;
  if (role === 'builder') return 2;
  if (role === 'viewer') return 1;
  return 0;
}

/** Confirm when crossing admin boundary or dropping below builder. */
function needsConfirm(fromEffective: string, toEffective: string): boolean {
  if (fromEffective === toEffective) return false;
  if (fromEffective === 'admin' || toEffective === 'admin') return true;
  if (rank(toEffective) < rank(fromEffective)) return true;
  return false;
}

function effectiveAfter(user: AuthUserAdmin, override: AppRole | null): AppRole {
  return (override ?? user.ldap_role) as AppRole;
}

export function UserRolesCard() {
  const [users, setUsers] = useState<AuthUserAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [newUsername, setNewUsername] = useState('');
  const [newOverride, setNewOverride] = useState<string>('builder');
  const [pending, setPending] = useState<{
    username: string;
    role_override: AppRole | null;
    from: string;
    to: string;
  } | null>(null);

  const load = () => {
    setLoading(true);
    auth
      .users()
      .then(setUsers)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load users'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const applyOverride = async (username: string, role_override: AppRole | null) => {
    setSaving(username);
    setError(null);
    try {
      const updated = await auth.setRoleOverride(username, role_override);
      setUsers((prev) => {
        const rest = prev.filter((u) => u.username !== updated.username);
        return [...rest, updated].sort((a, b) => a.username.localeCompare(b.username));
      });
      notifications.show({
        title: 'Role updated',
        message: role_override
          ? `${username} is now ${role_override} (local override).`
          : `${username} now follows the LDAP mapping.`,
        color: 'teal',
        icon: <IconCheck size={16} />,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed');
    } finally {
      setSaving(null);
      setPending(null);
    }
  };

  const requestOverride = (user: AuthUserAdmin, next: AppRole | null) => {
    const to = effectiveAfter(user, next);
    if (needsConfirm(user.role, to)) {
      setPending({ username: user.username, role_override: next, from: user.role, to });
      return;
    }
    void applyOverride(user.username, next);
  };

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    const username = newUsername.trim();
    if (!username) return;
    const override = (newOverride || null) as AppRole | null;
    // Creating an admin override always confirms.
    if (override === 'admin') {
      setPending({ username, role_override: override, from: 'none', to: 'admin' });
      return;
    }
    await applyOverride(username, override);
    setNewUsername('');
  };

  const roleSelect = (u: AuthUserAdmin) => (
    <Select
      size="xs"
      data={ROLE_OPTIONS}
      value={u.role_override ?? ''}
      disabled={saving === u.username}
      onChange={(value) => requestOverride(u, (value || null) as AppRole | null)}
      aria-label={`Override for ${u.username}`}
    />
  );

  return (
    <Card withBorder padding="lg" radius="md">
      <Group gap="sm" mb={4}>
        <IconUsers size={18} stroke={1.6} />
        <Title order={4}>User roles</Title>
      </Group>
      <Text size="sm" c="dimmed" mb="md">
        Local overrides take effect on the user&apos;s next API request — they do not wait for the
        access token to expire. Leave the override empty to follow LDAP groups.
      </Text>

      {error && (
        <Alert
          color="red"
          icon={<IconAlertCircle size={16} />}
          title="Users"
          mb="md"
          withCloseButton
          onClose={() => setError(null)}
        >
          {error}
        </Alert>
      )}

      {loading ? (
        <Group justify="center" py="md">
          <Loader size="sm" />
        </Group>
      ) : users.length === 0 ? (
        <Text size="sm" c="dimmed" mb="md">
          No users have signed in yet. You can still set an override below; it applies as soon as
          they authenticate.
        </Text>
      ) : (
        <>
          {/* Mobile cards */}
          <Stack gap="sm" hiddenFrom="sm" mb="md">
            {users.map((u) => (
              <Card key={u.username} withBorder padding="sm" radius="sm">
                <Stack gap="xs">
                  <div>
                    <Text size="sm" fw={600}>
                      {u.username}
                    </Text>
                    {u.display_name && (
                      <Text size="xs" c="dimmed">
                        {u.display_name}
                      </Text>
                    )}
                  </div>
                  <Group gap="xs">
                    <Text size="xs" c="dimmed">
                      LDAP
                    </Text>
                    <Badge variant="light" color={roleColor(u.ldap_role)}>
                      {u.ldap_role}
                    </Badge>
                    <Text size="xs" c="dimmed">
                      Effective
                    </Text>
                    <Badge color={roleColor(u.role)}>{u.role}</Badge>
                  </Group>
                  {roleSelect(u)}
                </Stack>
              </Card>
            ))}
          </Stack>

          {/* Desktop table */}
          <ScrollArea visibleFrom="sm" mb="md">
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>User</Table.Th>
                  <Table.Th>LDAP</Table.Th>
                  <Table.Th>Override</Table.Th>
                  <Table.Th>Effective</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {users.map((u) => (
                  <Table.Tr key={u.username}>
                    <Table.Td>
                      <Text size="sm" fw={600}>
                        {u.username}
                      </Text>
                      {u.display_name && (
                        <Text size="xs" c="dimmed">
                          {u.display_name}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light" color={roleColor(u.ldap_role)}>
                        {u.ldap_role}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{roleSelect(u)}</Table.Td>
                    <Table.Td>
                      <Badge color={roleColor(u.role)}>{u.role}</Badge>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        </>
      )}

      <form onSubmit={onCreate}>
        <Stack gap="sm">
          <Text size="sm" fw={500}>
            Set override for a username
          </Text>
          <Group align="flex-end" grow>
            <TextInput
              label="Username"
              placeholder="jsmith"
              value={newUsername}
              onChange={(e) => setNewUsername(e.currentTarget.value)}
              autoComplete="off"
            />
            <Select
              label="Override"
              data={ROLE_OPTIONS.filter((o) => o.value !== '')}
              value={newOverride}
              onChange={(v) => setNewOverride(v || 'builder')}
            />
            <Button type="submit" loading={saving === newUsername.trim()} disabled={!newUsername.trim()}>
              Save override
            </Button>
          </Group>
        </Stack>
      </form>

      <Modal
        opened={!!pending}
        onClose={() => setPending(null)}
        title="Confirm role change"
        centered
      >
        <Stack gap="md">
          <Text size="sm">
            Change <strong>{pending?.username}</strong> from <strong>{pending?.from}</strong> to{' '}
            <strong>{pending?.to}</strong>?
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPending(null)}>
              Keep current
            </Button>
            <Button
              color="red"
              loading={!!pending && saving === pending.username}
              onClick={() => {
                if (!pending) return;
                const { username, role_override } = pending;
                void applyOverride(username, role_override).then(() => {
                  if (newUsername.trim() === username) setNewUsername('');
                });
              }}
            >
              Confirm change
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}
