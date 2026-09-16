import { createTheme, MantineColorsTuple, DefaultMantineColor } from '@mantine/core';

declare module '@mantine/core' {
  export interface MantineThemeColorsOverride {
    colors: Record<DefaultMantineColor, MantineColorsTuple>;
  }
}

// OpenVox red — deep crimson with definition on both light and dark surfaces.
const ovred: MantineColorsTuple = [
  '#fdecea', '#f5c4bf', '#ec9a92', '#e26f63', '#d94a3a',
  '#C0392B', '#a83225', '#8f2a1f', '#762219', '#5d1a14',
];

const base = {
  fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  fontFamilyMonospace: '"IBM Plex Mono", ui-monospace, monospace',
  headings: {
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    fontWeight: '650',
  },
  defaultRadius: 'md' as const,
  primaryColor: 'ovred' as const,
  colors: { ovred },
  components: {
    Button: { defaultProps: { color: 'ovred' } },
    ActionIcon: { defaultProps: { color: 'ovred' } },
    Badge: { defaultProps: { color: 'ovred' } },
    NavLink: { defaultProps: { color: 'ovred' } },
  },
  other: {
    // CSS variable hooks the AppShell and login page read for the accent.
    accent: '#C0392B',
    accentSoft: 'rgba(192,57,43,0.10)',
  },
};

export const lightTheme = createTheme({
  ...base,
  primaryColor: 'ovred',
  other: { ...base.other, surface: '#f3f5f8', line: '#e2e6ee' },
});

export const darkTheme = createTheme({
  ...base,
  primaryColor: 'ovred',
  other: { ...base.other, surface: '#12131c', line: '#2a2d3a' },
});

export type AppTheme = 'light' | 'dark';
