import type { Config } from 'tailwindcss';

export default {
  darkMode: 'class', // always-on via <html class="dark">
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          0: '#0B0D17',
          1: '#11151F',
        },
        surface: {
          1: '#1A2030',
          2: '#232A3D',
          3: '#2D3650',
        },
        border: {
          DEFAULT: '#1F2937',
        },
        text: {
          hi: '#FFFFFF',
          0: '#E5E7EB',
          1: '#9CA3AF',
          2: '#6B7280',
        },
        accent: {
          DEFAULT: '#60A5FA',
          strong: '#3B82F6',
        },
        warm: {
          DEFAULT: '#FBBF24',
          hot: '#F97316',
        },
        success: '#22C55E',
        danger: '#EF4444',
        info: '#A855F7',
        // shadcn slots (referenced by installed primitives)
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
      },
      fontFamily: {
        sans: ['"Inter Variable"', 'Inter', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono Variable"', '"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: {
        lg: '12px',
        xl: '16px',
        '2xl': '16px',
      },
      transitionDuration: {
        fast: '120ms',
        base: '200ms',
        slow: '300ms',
      },
      transitionTimingFunction: {
        standard: 'cubic-bezier(0.2, 0.0, 0, 1)',
      },
    },
  },
  plugins: [],
} satisfies Config;
