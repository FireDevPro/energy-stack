/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        // Single distinctive accent for "this is winning right now."
        // Reserved exclusively for the winning lane bar + active-state
        // emphasis. Never used for tier / arm / freshness encoding.
        radium: {
          DEFAULT: '#a3ff70',
          400: '#bdff8e',
          500: '#a3ff70',
          600: '#7be63a',
        },
      },
      keyframes: {
        'pulse-slow': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
        march: {
          from: { strokeDashoffset: '0' },
          to: { strokeDashoffset: '-12' },
        },
      },
      animation: {
        'pulse-slow': 'pulse-slow 2.4s ease-in-out infinite',
        march: 'march 4.5s linear infinite',
      },
    },
  },
  plugins: [],
}
