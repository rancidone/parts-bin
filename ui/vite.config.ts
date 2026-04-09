import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    allowedHosts: ['localhost', 'dadbook.local', 'dadbook.brownfamily.house', 'parts.brownfamily.house'],
    proxy: {
      '/chat':      'http://localhost:8000',
      '/inventory': 'http://localhost:8000',
      '/health':    'http://localhost:8000',
    },
  },
})
