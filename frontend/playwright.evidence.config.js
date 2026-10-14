// 6R18 证据生成配置：与 headless 一致，但**不过滤**截图/证据脚本（phase6r18-shots 等），供显式运行生成证据。
// 运行：node_modules\.bin\playwright test -c playwright.evidence.config.js phase6r18-shots.spec.js
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 90000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
