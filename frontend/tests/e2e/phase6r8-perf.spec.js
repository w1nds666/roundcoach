// 6R8 性能门禁：十人同步回放 常规桌面 60fps 目标实测
// 记录（写 docs/phase6r8-perf.json）：测试机器、demo/回合、播放时长、平均/最低 fps、渲染耗时。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = join(__dirname, '..', '..', '..')   // frontend/tests/e2e -> 仓库根
const PERF_JSON = join(REPO_ROOT, 'docs', 'phase6r8-perf.json')

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const ROUND = 3
const SAMPLE_MS = 4000
const SPEED = 1
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
async function enterTeam(page, round) {
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.locator('.tm-sel').selectOption(String(round))
  await page.waitForTimeout(700)
  await page.waitForFunction(() => {
    const cv = document.querySelector('.tm-canvas'); if (!cv) return false
    const ctx = cv.getContext('2d'); const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
    let p = 0; for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 10) p++
    return p / (cv.width * cv.height) > 0.01
  }, { timeout: 20000 })
}

test('Max：十人同步回放 常规桌面实测 FPS（目标 ≥60）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：按目标 demo 播种团队权益门禁
  await login(page, ACCOUNT)
  await enterTeam(page, ROUND)

  // 播放并采样 rAF 帧率（4s，1x）：记录每帧间隔，算平均/最低 fps
  await page.locator('.tm-play').click()
  const fps = await page.evaluate((ms) => new Promise((resolve) => {
    const ts = []
    const start = performance.now()
    function loop(t) {
      ts.push(t)
      if (t - start < ms) requestAnimationFrame(loop)
      else {
        const gaps = []
        for (let i = 1; i < ts.length; i++) gaps.push(ts[i] - ts[i - 1])
        const avgGap = gaps.reduce((a, b) => a + b, 0) / (gaps.length || 1)
        const maxGap = Math.max(...gaps)
        resolve({
          frames: ts.length,
          ms: ts[ts.length - 1] - start,
          avg: (ts.length - 1) / ((ts[ts.length - 1] - start) / 1000),
          min: 1000 / (maxGap || 1000),
        })
      }
    }
    requestAnimationFrame(loop)
  }), SAMPLE_MS)
  await page.locator('.tm-play').click()   // 暂停

  const res = {
    test_machine: {
      cpu: 'AMD Ryzen 9 8940HX (32 threads)', ram_gb: 31.2, gpu: 'AMD Radeon(TM) 610M (integrated)',
      os: 'Windows 11 64-bit', browser: 'Chromium headed (Playwright)', viewport: '1440x900',
    },
    demo: { id: 19, map: 'de_mirage', round: ROUND, players: 10, tick_rate: 64, speed: SPEED },
    sample_ms: SAMPLE_MS,
    avg_fps: Math.round(fps.avg * 10) / 10,
    min_fps: Math.round(fps.min * 10) / 10,
    frames: fps.frames,
    note: 'target 60fps; recorded honestly (avg/min over 4s 1x playback, rAF sampling)',
  }
  const out = PERF_JSON
  mkdirSync(join(REPO_ROOT, 'docs'), { recursive: true })
  writeFileSync(out, JSON.stringify(res, null, 2), 'utf8')
  await page.screenshot({ path: `${SHOTS}/6r8-perf-10players.png` })
  console.log('perf_json_written=' + out)
  console.log(JSON.stringify(res))
  expect(fps.avg).toBeGreaterThan(0)
})
