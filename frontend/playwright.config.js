// Phase 4B 独立浏览器验收（Playwright，独立 Chromium 实例，不碰 DSH 驾驶舱标签）
// 运行：npx playwright test
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60000,
  fullyParallel: false,        // 顺序执行（避免截图/账号干扰）
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost',
    viewport: { width: 1440, height: 900 },
    headless: false,           // headed：独立可见实例（用户桌面）
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
