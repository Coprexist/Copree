import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  base: process.env.BASE_URL || '/',
  build: { sourcemap: process.env.BASE_URL ? true : false },
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
    // rehype-katex 自带嵌套 katex（它声明 ^0.16.0，顶层是 0.17.0 → npm 重复安装），
    // 导致 mhchem 注册的实例 ≠ rehype-katex 使用的实例（\ce 永远未定义）。
    // dedupe 强制所有 'katex' 解析到顶层单实例（KaTeX 官方推荐，对 npm install 复现同样生效）
    dedupe: ['katex'],
  },
  optimizeDeps: {
    // katex 与 mhchem 一起预构建：mhchem 的 \ce 宏必须注册到 rehype-katex 使用的
    // 同一个 katex 实例（否则双包实例，\ce 未定义；且直接 import 会崩 React 树）
    include: ['katex', 'katex/dist/contrib/mhchem.mjs'],
  },
  server: {
    port: 3000,
    host: true,
    allowedHosts: true,
    watch: {
      usePolling: true,
    },
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        configure: (proxy: any) => {
          // FastAPI 对尾斜杠不匹配的路径会 307，而 changeOrigin 把 Host 改成了 backend:8000 ——
          // 重定向于是指向 http://backend:8000/... ，浏览器解析不了这个内网名，请求直接 "Failed to fetch"。
          // 不动请求路径（有的路由确实只注册了带尾斜杠的形式），只把 Location 换回浏览器可用的地址。
          proxy.on('proxyRes', (proxyRes: any) => {
            const loc = proxyRes.headers?.location
            if (typeof loc !== 'string' || !/^https?:\/\//.test(loc)) return
            try {
              const u = new URL(loc)
              proxyRes.headers.location = `/api${u.pathname}${u.search}`
            } catch {
              /* 解析不了就原样透传 */
            }
          })
        },
      },
      '/world/': {
        target: 'http://backend:8000',
        changeOrigin: true,
        ws: true,
      },
      '/ws': {
        target: 'ws://backend:8000',
        ws: true,
      },
      '/federation': {
        target: 'http://backend:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
