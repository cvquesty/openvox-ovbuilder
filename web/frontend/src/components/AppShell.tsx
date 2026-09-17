import { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router';
import {
  AppShell as MantineAppShell,
  NavLink,
  Title,
  Group,
  Text,
  Burger,
  ScrollArea,
  Box,
  Badge,
  ActionIcon,
  Tooltip,
} from '@mantine/core';
import {
  IconRocket,
  IconListDetails,
  IconLogout,
  IconUser,
  IconSun,
  IconMoon,
  IconActivity,
  IconServer,
  IconSettings,
} from '@tabler/icons-react';
import { useAuth } from '../hooks/AuthContext';
import { useAppTheme } from '../hooks/ThemeContext';

interface NavItem {
  label: string;
  icon: any;
  path: string;
  roles?: string[];
}

const NAV: NavItem[] = [
  { label: 'Build a VM', icon: IconRocket, path: '/build', roles: ['admin', 'builder'] },
  { label: 'Virtual machines', icon: IconServer, path: '/vms', roles: ['admin', 'builder'] },
  { label: 'My Builds', icon: IconListDetails, path: '/jobs' },
  { label: 'Configuration', icon: IconSettings, path: '/settings', roles: ['admin'] },
];

export function AppShellLayout() {
  const [opened, setOpened] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const { isDark, toggle } = useAppTheme();

  const logoSrc = '/openvox-logo-red.svg';
  const titleColor = !isDark ? '#0f172a' : undefined;
  const homePath = user && (user.role === 'admin' || user.role === 'builder') ? '/build' : '/jobs';

  const visibleNav = NAV.filter((item) => {
    if (!item.roles) return true;
    return !!user && item.roles.includes(user.role);
  });

  const renderNav = (item: NavItem) => {
    const Icon = item.icon;
    const active = location.pathname === item.path || location.pathname.startsWith(item.path + '/');
    return (
      <NavLink
        key={item.path}
        label={item.label}
        leftSection={<Icon size={18} stroke={1.6} />}
        active={active}
        onClick={() => { navigate(item.path); setOpened(false); }}
        mb={4}
      />
    );
  };

  return (
    <>
      <a href="#main-content" className="ov-skip-link">Skip to content</a>
      <MantineAppShell
        header={{ height: 56 }}
        navbar={{ width: 248, breakpoint: 'sm', collapsed: { mobile: !opened } }}
        padding="lg"
      >
        <MantineAppShell.Header style={{ borderBottom: '1px solid var(--ov-line)' }}>
          <Group h="100%" px="lg" justify="space-between">
            <Group gap="xs">
              <Burger opened={opened} onClick={() => setOpened(!opened)} hiddenFrom="sm" size="sm" aria-label="Open navigation" />
              <Group gap={12} wrap="nowrap" style={{ cursor: 'pointer' }} onClick={() => navigate(homePath)}>
                <img src={logoSrc} alt="OpenVox" style={{ height: 30, width: 30, flexShrink: 0, display: 'block' }} />
                <Title order={4} c={titleColor} style={{ fontWeight: 650, whiteSpace: 'nowrap', letterSpacing: '-0.02em' }}>
                  OV Builder
                </Title>
              </Group>
            </Group>
            {user && (
              <Group gap="sm">
                <Badge variant="outline" color="gray" size="sm" visibleFrom="sm">
                  <Group gap={4}><IconActivity size={12} />{user.role}</Group>
                </Badge>
                <Tooltip label={isDark ? 'Light mode' : 'Dark mode'}>
                  <ActionIcon variant="subtle" color="gray" onClick={toggle} aria-label="Toggle theme">
                    {isDark ? <IconSun size={18} stroke={1.6} /> : <IconMoon size={18} stroke={1.6} />}
                  </ActionIcon>
                </Tooltip>
                <Badge variant="outline" color="gray" size="sm" visibleFrom="sm">
                  <Group gap={4}><IconUser size={12} />{user.username}</Group>
                </Badge>
                <Tooltip label="Sign out">
                  <ActionIcon variant="subtle" color="gray" onClick={logout} aria-label="Sign out">
                    <IconLogout size={18} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            )}
          </Group>
        </MantineAppShell.Header>

        <MantineAppShell.Navbar p="sm" style={{ borderRight: '1px solid var(--ov-line)' }}>
          <MantineAppShell.Section grow component={ScrollArea}>
            {visibleNav.map(renderNav)}
          </MantineAppShell.Section>
          <MantineAppShell.Section>
            <Box pt="sm" px="xs">
              <Text size="xs" c="dimmed" fw={500}>v{__APP_VERSION__}</Text>
            </Box>
          </MantineAppShell.Section>
        </MantineAppShell.Navbar>

        <MantineAppShell.Main id="main-content">
          <Outlet />
        </MantineAppShell.Main>
      </MantineAppShell>
    </>
  );
}
