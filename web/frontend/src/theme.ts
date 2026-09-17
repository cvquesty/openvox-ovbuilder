import { createTheme, MantineColorsTuple, DefaultMantineColor } from '@mantine/core';

declare module '@mantine/core' {
  export interface MantineThemeColorsOverride {
    colors: Record<DefaultMantineColor, MantineColorsTuple>;
  }
}

// Brand mark red (logo stays crimson; not used as primary).
const ovred: MantineColorsTuple = [
  '#fdecea', '#f5c4bf', '#ec9a92', '#e26f63', '#d94a3a',
  '#C0392B', '#a83225', '#8f2a1f', '#762219', '#5d1a14',
];

// OpenVox GUI light primary — vpblue (~#0D6EFD).
const vpblue: MantineColorsTuple = [
  '#e7f1ff', '#cfe4ff', '#9ec5fe', '#6ea8fe', '#3d8bfd',
  '#0D6EFD', '#0b5ed7', '#0a58ca', '#084298', '#052c65',
];

// OpenVox GUI dark primary — orange (~#EC8622).
const vorange: MantineColorsTuple = [
  '#fff4e6', '#ffe8cc', '#ffd8a8', '#ffc078', '#ffa94d',
  '#EC8622', '#e67700', '#d9480f', '#c2410c', '#9c3410',
];

const sharedComponents = {
  Badge: { defaultProps: { tt: 'none' as const } },
  Button: { defaultProps: {} },
  ActionIcon: { defaultProps: {} },
  NavLink: { defaultProps: {} },
};

const base = {
  fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  fontFamilyMonospace: '"IBM Plex Mono", ui-monospace, monospace',
  headings: {
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    fontWeight: '650',
  },
  defaultRadius: 'md' as const,
  colors: { ovred, vpblue, vorange },
  other: {
    accent: '#0D6EFD',
    accentSoft: 'rgba(13,110,253,0.10)',
  },
};

export const lightTheme = createTheme({
  ...base,
  primaryColor: 'vpblue',
  colors: { ovred, vpblue, vorange },
  components: {
    ...sharedComponents,
    Button: { defaultProps: { color: 'vpblue' } },
    ActionIcon: { defaultProps: { color: 'vpblue' } },
    Badge: { defaultProps: { color: 'vpblue', tt: 'none' } },
    NavLink: { defaultProps: { color: 'vpblue' } },
  },
  other: {
    accent: '#0D6EFD',
    accentSoft: 'rgba(13,110,253,0.10)',
    surface: '#f3f5f8',
    line: '#e2e6ee',
  },
});

export const darkTheme = createTheme({
  ...base,
  primaryColor: 'vorange',
  colors: { ovred, vpblue, vorange },
  components: {
    ...sharedComponents,
    Button: { defaultProps: { color: 'vorange' } },
    ActionIcon: { defaultProps: { color: 'vorange' } },
    Badge: { defaultProps: { color: 'vorange', tt: 'none' } },
    NavLink: { defaultProps: { color: 'vorange' } },
  },
  other: {
    accent: '#EC8622',
    accentSoft: 'rgba(236,134,34,0.14)',
    surface: '#12131c',
    line: '#2a2d3a',
  },
});

export type AppTheme = 'light' | 'dark';
