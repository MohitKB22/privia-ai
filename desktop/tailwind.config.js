/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // A restrained graphite scale. One accent, used sparingly.
        graphite: {
          950: '#0a0b0d',
          900: '#101214',
          850: '#15181b',
          800: '#1b1f23',
          750: '#22272c',
          700: '#2a3036',
          600: '#3a424a',
          500: '#535d67',
          400: '#7b858f',
          300: '#a3acb5',
          200: '#c9cfd5',
          100: '#e6e9ec',
        },
        accent: {
          DEFAULT: '#5eb3a1',
          soft: 'rgba(94, 179, 161, 0.14)',
          line: 'rgba(94, 179, 161, 0.34)',
        },
        danger: { DEFAULT: '#d97066', soft: 'rgba(217, 112, 102, 0.14)' },
        caution: { DEFAULT: '#d9a441', soft: 'rgba(217, 164, 65, 0.14)' },
      },
      fontFamily: {
        sans: ['Inter', 'SF Pro Text', '-apple-system', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['SF Mono', 'JetBrains Mono', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: { xl: '0.75rem', '2xl': '1rem' },
      boxShadow: {
        panel: '0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 30px -12px rgba(0,0,0,0.7)',
        lift: '0 12px 40px -16px rgba(0,0,0,0.8)',
      },
      backdropBlur: { xs: '2px' },
      keyframes: {
        'fade-in': { from: { opacity: '0', transform: 'translateY(3px)' }, to: { opacity: '1', transform: 'none' } },
        'slide-up': { from: { opacity: '0', transform: 'translateY(10px)' }, to: { opacity: '1', transform: 'none' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
        'pulse-soft': { '0%,100%': { opacity: '0.55' }, '50%': { opacity: '1' } },
      },
      animation: {
        'fade-in': 'fade-in 160ms ease-out',
        'slide-up': 'slide-up 200ms cubic-bezier(0.22,1,0.36,1)',
        shimmer: 'shimmer 1.6s infinite',
        'pulse-soft': 'pulse-soft 1.8s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
