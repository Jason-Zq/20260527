import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 本地 8000 被占用时可用 VITE_API_TARGET 指向其他后端端口,如:
//   VITE_API_TARGET=http://localhost:8002 npm run dev
const apiTarget = process.env.VITE_API_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true
      },
      '/uploads': {
        target: apiTarget,
        changeOrigin: true
      }
    },
    host: '0.0.0.0', // 允许局域网访问
    allowedHosts: ['599139sw33.zicp.fun'], // 允许穿透域名
  }
})
