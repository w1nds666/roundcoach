// 6R11 视觉/结构门禁与截图矩阵：无头运行（不打扰桌面），供 AI 生成 before/after 截图与 DOM 结构断言。
// 运行：npx playwright test -c playwright.headless.config.js
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 90000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  // 历史 before/after 证据脚本依赖独立 dev 服务并写固定截图路径，不作为默认 E2E 断言收集，
  // 避免在错误状态下重跑覆盖 before 证据；这些脚本仅在显式指定时运行。
  testIgnore: [
    '**/_6r17_before_shots.spec.js',
    '**/phase6r17-shots.spec.js',
    '**/phase6r17-perf.spec.js',
    '**/phase6r18-shots.spec.js',
    '**/phase6r21-shots.spec.js',
    '**/phase6r21b-vision-matrix.spec.js',
    '**/phase6r21c-baseline.spec.js',
    '**/phase6r21f-f1-shots.spec.js',
    '**/phase6r21f-f2-shots.spec.js',
  ],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
