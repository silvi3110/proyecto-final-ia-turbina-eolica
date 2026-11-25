import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000'
const demoMode = process.env.API_DEMO_MODE === '1'
const apiUsername = process.env.API_USERNAME
const apiPassword = process.env.API_PASSWORD
const authorization = !demoMode && apiUsername && apiPassword
  ? `Basic ${Buffer.from(`${apiUsername}:${apiPassword}`, 'utf8').toString('base64')}`
  : null

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        configure: (proxy) => {
          if (authorization) {
            proxy.on('proxyReq', (proxyRequest) => {
              proxyRequest.setHeader('Authorization', authorization)
            })
          }
        },
      },
    },
  },
})