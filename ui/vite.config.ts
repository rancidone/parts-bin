import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const allowedHosts = env.VITE_ALLOWED_HOSTS
    ? env.VITE_ALLOWED_HOSTS.split(',').map((h) => h.trim())
    : ['localhost']

  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      allowedHosts,
      proxy: {
        '/chat':      'http://localhost:8000',
        '/inventory': 'http://localhost:8000',
        '/health':    'http://localhost:8000',
        '/jlcparts':  'http://localhost:8000',
      },
    },
  }
})
