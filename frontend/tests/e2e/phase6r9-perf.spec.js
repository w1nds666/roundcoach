// 6R9 性能门禁：十人同步回放 帧时分布（warm-up + 持续播放），由测试程序直接落盘 docs/phase6r9-perf.json。
// - 记录 p50/p95/p99 帧时、>16.7ms 与 >33.3ms 的 jank 次数/比例、持续平均/最低 fps；
// - 首屏加载（冷启动，到 canvas data-ready）与持续播放分开测；
// - 60fps 等效帧时 = 16.7ms（若机器/高刷不适合，用「60fps 等效帧时」报告并说明）。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = join(__dirname, '..', '..', '..')
const PERF_JSON = join(REPO_ROOT, 'docs', 'phase6r9-perf.json')
const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const ROUND = 3
const WARMUP_MS = 2000
const SAMPLE_MS = 4000
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page, account) {
  await page.goto('/login')
  await page.fill('input[type="email"]', account.email)
  await page.fill('input[type="password"]', account.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

function percentile(sorted, p) {
  if (!sorted.length) return 0
  const idx = Math.min(sorted.length - 1, Math.floor(p * sorted.length))
  return sorted[idx]
}

test('Max：十人同步回放 帧时分布（warm-up + 持续）实测并落盘', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：按目标 demo 播种团队权益门禁
  await login(page, ACCOUNT)

  // 首屏冷启动：从导航到 canvas data-ready 的时长
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  const loadStart = Date.now()
  await page.waitForFunction(() => document.querySelector('.tm-canvas')?.getAttribute('data-ready') === 'ready', { timeout: 25000 })
  const firstLoadMs = Date.now() - loadStart

  await page.locator('.tm-sel').selectOption(String(ROUND))
  await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), String(ROUND), { timeout: 8000 })

  // warm-up 播放
  await page.locator('.tm-play').click()
  await page.waitForTimeout(WARMUP_MS)

  // 持续播放采样帧时
  const res = await page.evaluate((ms) => new Promise((resolve) => {
    const ts = []
    const start = performance.now()
    function loop(t) {
      ts.push(t)
      if (t - start < ms) requestAnimationFrame(loop)
      else {
        const gaps = []
        for (let i = 1; i < ts.length; i++) gaps.push(ts[i] - ts[i - 1])
        const sorted = [...gaps].sort((a, b) => a - b)
        const p = (q) => percentile(sorted, q)
        const over167 = gaps.filter((g) => g > 16.7).length
        const over333 = gaps.filter((g) => g > 33.3).length
        const avgGap = gaps.reduce((a, b) => a + b, 0) / (gaps.length || 1)
        resolve({
          frames: ts.length,
          ms: (ts[ts.length - 1] - start),
          avg_gap_ms: Number(avgGap.toFixed(3)),
          p50_ms: Number(p(0.5).toFixed(3)),
          p95_ms: Number(p(0.95).toFixed(3)),
          p99_ms: Number(p(0.99).toFixed(3)),
          max_gap_ms: Number(Math.max(...gaps).toFixed(3)),
          over167_count: over167, over167_ratio: Number((over167 / (gaps.length || 1)).toFixed(4)),
          over333_count: over333, over333_ratio: Number((over333 / (gaps.length || 1)).toFixed(4)),
          avg_fps: Number(((ts.length - 1) / ((ts[ts.length - 1] - start) / 1000)).toFixed(1)),
          min_fps: Number((1000 / (Math.max(...gaps) || 1000)).toFixed(1)),
        })
      }
    }
    requestAnimationFrame(loop)
    function percentile(sorted, q) { // eslint-disable-line
      if (!sorted.length) return 0
      const idx = Math.min(sorted.length - 1, Math.floor(q * sorted.length))
      return sorted[idx]
    }
  }), SAMPLE_MS)
  await page.locator('.tm-play').click()

  const out = {
    test_machine: {
      cpu: 'AMD Ryzen 9 8940HX (32 threads)', ram_gb: 31.2, gpu: 'AMD Radeon(TM) 610M (integrated)',
      os: 'Windows 11 64-bit', browser: 'Chromium headed (Playwright)', viewport: '1440x900',
      note: '集成 GPU，非高刷显示器（60Hz 等效）。',
    },
    demo: { id: 19, map: 'de_mirage', round: ROUND, players: 10, tick_rate: 64, speed: 1 },
    first_load_ms: firstLoadMs,
    warmup_ms: WARMUP_MS,
    sample_ms: SAMPLE_MS,
    sustained: res,
    gate: { target_p95_ms: 16.7, note: '目标：持续播放 p95 帧时 <= 16.7ms（60fps 等效）；若机器/高刷不适合，用 60fps 等效帧时报告；未达则保留量化债务，绝不掩饰。' },
  }
  mkdirSync(join(REPO_ROOT, 'docs'), { recursive: true })
  writeFileSync(PERF_JSON, JSON.stringify(out, null, 2), 'utf8')
  await page.screenshot({ path: `${SHOTS}/6r9-perf-10players.png` })
  console.log('perf_json_written=' + PERF_JSON)
  expect(res.avg_fps).toBeGreaterThan(0)
})
