import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Poppins', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      colors: {
        brand: {
          50: '#f3f9f7',
          100: '#d6eee7',
          200: '#b1ddd0',
          300: '#88c7b4',
          400: '#5cab95',
          500: '#3d8f79',
          600: '#2f7262',
          700: '#285a4f',
          800: '#234840',
          900: '#1f3d36'
        }
      },
      boxShadow: {
        soft: '0 10px 40px rgba(19, 49, 43, 0.14)',
      },
    },
  },
  plugins: [typography],
}
