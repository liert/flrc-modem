/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        rf: {
          bg: '#0d1117',
          surface: '#161b22',
          panel: '#1f242c',
          border: '#30363d',
          accent: '#00e5ff',
          neon: '#00ff66',
          warning: '#f59e0b',
          danger: '#ef4444',
          text: '#f0f6fc',
          muted: '#8b949e',
        }
      },
      fontFamily: {
        mono: ['Consolas', 'Cascadia Mono', 'SFMono-Regular', 'Menlo', 'Monaco', 'Liberation Mono', 'Courier New', 'monospace'],
      }
    },
  },
  plugins: [],
}
