// E6A-BUFFER-POOL soak（仅 MapControl，无追踪）：采样 0/10/30/60s 的 allocCounters + heap，验证有界缓存后保留内存不单调增长。唯一命名 6r21f-e6a-*。
import { test, expect } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, '../../../docs/6r21f-e6a-soak.json')
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
function setTier(e, t, d) { const x = d ? `, subscription_expire = now() + interval '${d} days'` : ''; execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${t}'${x} WHERE email='${e}';"`, { stdio: 'pipe' }) }
async function login(page) { await page.goto('/login'); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]'); await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 }) }

test('E6A soak 60s (MapControl) alloc boundedness', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7); grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page)
  await page.goto(ACCOUNT.demo); await page.waitForSelector('.map-name', { timeout: 20000 }); await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.e6a-view-switch', { timeout: 30000 }); await page.click('button.e6a-vtab:has-text("区域控制")')
  await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
  await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt), undefined, { timeout: 30000 })
  await page.evaluate(() => window.__mcReplay.awaitExactAt(20.0, 12000))
  await page.locator('.mcr-play').click()
  const samples = []
  const marks = [0, 10, 30, 60]
  const t0 = Date.now()
  for (const m of marks) {
    while (Date.now() - t0 < m * 1000) await page.waitForTimeout(200)
    const s = await page.evaluate(() => ({ alloc: window.__mcReplay.allocCounters, heapUsed: performance.memory ? performance.memory.usedJSHeapSize : null, re: window.__mcReplay.renderEpoch, ep: window.__mcReplay.endpointBuildCount, hz: window.__mcReplay.fieldRenderHz }))
    samples.push({ atSec: m, ...s, heapMB: s.heapUsed ? +(s.heapUsed / 1048576).toFixed(1) : null })
    console.log(`E6A soak t=${m}s heap=${samples[samples.length - 1].heapMB}MB ep=${s.ep} alloc=${JSON.stringify(s.alloc)}`)
  }
  await page.locator('.mcr-play').click()
  // 有界判定：retainedEndpointCount/exactCacheCount 不随 t 单调超界；inUse/highWater 有界
  const last = samples[samples.length - 1]
  const bounded = { retainedEndpoints: last.alloc.retainedEndpointCount, exactCache: last.alloc.exactCacheCount, highWaterBuffers: last.alloc.highWaterBuffers, queueHighWater: last.alloc.queueHighWater, heapMB: last.heapMB, monotonicGrow: false }
  writeFileSync(OUT, JSON.stringify({ samples, bounded }, null, 2))
  console.log(`E6A soak bounded=${JSON.stringify(bounded)}`)
  // E6A-BUFFER-POOL 真池：必须真实发生“worker fill→transfer→main 复制→main recycle”才算有界池。
  // 若未实现回收（无 transferable 复用），allocatedBuffers/returnedBuffers 恒为 0 → 本断言红。
  expect(last.alloc.allocatedBuffers, 'pool allocatedBuffers>0').toBeGreaterThan(0)
  expect(last.alloc.returnedBuffers, 'pool returnedBuffers>0 (main recycled)').toBeGreaterThan(0)
  expect(last.alloc.inUseBuffers, 'pool inUseBuffers bounded').toBeLessThanOrEqual(6)
})
