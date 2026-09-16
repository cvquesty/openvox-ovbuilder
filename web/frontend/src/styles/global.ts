import type { MantineTheme } from '@mantine/core';

// Shared CSS custom properties so pages can reference the accent without
// hard-coding hex values. Mirrors the OpenVox GUI's --ov-* convention.
export const globalStyles = (theme: MantineTheme) => ({
  '*, *::before, *::after': { boxSizing: 'border-box' },
  body: {
    margin: 0,
    backgroundColor: 'var(--mantine-color-body)',
    color: 'var(--mantine-color-text)',
    WebkitFontSmoothing: 'antialiased',
  },
  ':root': {
    '--ov-accent': theme.other?.accent ?? '#C0392B',
    '--ov-accent-soft': theme.other?.accentSoft ?? 'rgba(192,57,43,0.10)',
    '--ov-line': theme.other?.line ?? '#e2e6ee',
    '--ov-surface': theme.other?.surface ?? '#f3f5f8',
  },
});
