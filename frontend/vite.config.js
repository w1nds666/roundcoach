import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 6R21F-E6B-NAVMESH-PRODUCT-GATE：NavMesh 不再经 Vite dev-only `/__dev__/vrf-navmesh` 路由或
// repo 外环境变量（VRF_NAVMESH_JSON）提供，改由受控后端产品路由 GET /api/maps/{map}/navmesh 稳定提供
// （见 backend/app/main.py + app/services/navmesh.py；版本化 artifact 在 backend/config/navmesh/）。
// 因此移除原 `vrf-devmode-navmesh` 中间件（该中间件在 headed 下会因 Vite dev 中间件 flaky 回退 SPA HTML，
// 导致地图控件挂载不稳——E6A 已记录）。以下仅保留 dev 的 /api、/health 反代到 nginx:80（产品链路）。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // /demo/* 与静态资源由 Vite 直接服务；API 始终经过 nginx，保留分享限流、
      // 代理头和产品路由语义。使用 loopback-only :8080，避开 Windows/WSL 的
      // wslrelay 偶发抢占 localhost:80 后返回空响应（Vite 会把它包装成 HTTP 500）。
      // production overlay 会清空宿主端口，因此此入口只存在于本地开发环境。
      '/api': 'http://127.0.0.1:8080',
      '/health': 'http://127.0.0.1:8080',
    },
  },
})
