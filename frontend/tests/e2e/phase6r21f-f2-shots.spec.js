// 6R21F-F2 视觉证据截图采集（before/after）。
// 以 TEST_TAG=before|after 区分；对 dev(5173 当前源码) 与 nginx 产品链分别截图。
// 覆盖：Dashboard(/)、Upload(/upload)、Login(/login)，五视口。产出 docs/visual-evidence/6r21f-f2-<src>-<tag>-<file>.png。
// 运行示例：
//   $env:TEST_TAG='before'; npx playwright test -c playwright.headless.config.js phase6r21f-f2-shots.spec.js
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const TAG = process.env.TEST_TAG || 'before'
const OUT = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(OUT, { recursive: true })
const S = (name) => resolve(OUT, `6r21f-f2-${name}-${TAG}.png`)
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const SOURCES = [
  { src: 'dev', base: process.env.SHOT_DEV_BASE || 'http://localhost:5173' },
  { src: 'prod', base: process.env.SHOT_PROD_BASE || 'http://localhost' },
].filter(({ src }) => !process.env.SHOT_SOURCE || process.env.SHOT_SOURCE === src)
const VIEWS = [
  { label: 'desktop1280', width: 1280, height: 800 },
  { label: 'desktop1081', width: 1081, height: 800 },
  { label: 'desktop900', width: 900, height: 800 },
  { label: 'mobile390', width: 390, height: 844 },
  { label: 'mobile360', width: 360, height: 740 },
]
test.setTimeout(420000)

function overflowInfo(page) {
  return page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
}

async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
}

for (const src of SOURCES) {
  const BASE = src.base
  test.describe(`6R21F-F2 ${TAG} shots ${src.src}`, () => {
    test('Dashboard + Upload (auth) 五视口', async ({ browser }) => {
      for (const v of VIEWS) {
        const ctx = await browser.newContext({ viewport: { width: v.width, height: v.height } })
        const page = await ctx.newPage()
        const wp = (p) => p.replace(BASE, '')
        await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
        await page.fill('input[type="email"]', ACCOUNT.email)
        await page.fill('input[type="password"]', ACCOUNT.password)
        await page.click('button[type="submit"]')
        await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })

        // Dashboard
        await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
        await page.waitForSelector('.page-title', { timeout: 30000 })
        await page.waitForTimeout(600)
        await page.screenshot({ path: S(`${src.src}-${v.label}-dashboard`), fullPage: true })
        const dw = await overflowInfo(page)
        console.log(`F2 ${TAG} ${src.src} ${v.label} dashboard scrollW=${dw.scroll} clientW=${dw.client} ok=${dw.scroll <= dw.client + 1}`)
        expect(dw.scroll, `${src.src}/${v.label} dashboard 无横滚`).toBeLessThanOrEqual(dw.client + 1)

        // Upload
        await page.goto(`${BASE}/upload`, { waitUntil: 'networkidle' })
        await page.waitForSelector('.page-title', { timeout: 30000 })
        await page.waitForTimeout(400)
        await page.screenshot({ path: S(`${src.src}-${v.label}-upload`), fullPage: true })
        const uw = await overflowInfo(page)
        console.log(`F2 ${TAG} ${src.src} ${v.label} upload scrollW=${uw.scroll} clientW=${uw.client} ok=${uw.scroll <= uw.client + 1}`)
        expect(uw.scroll, `${src.src}/${v.label} upload 无横滚`).toBeLessThanOrEqual(uw.client + 1)

        await ctx.close()
      }
    })

    test('Login (guest) 五视口', async ({ browser }) => {
      for (const v of VIEWS) {
        const ctx = await browser.newContext({ viewport: { width: v.width, height: v.height } })
        const page = await ctx.newPage()
        await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
        await page.waitForSelector('.auth-card, .card', { timeout: 20000 })
        await page.waitForTimeout(400)
        await page.screenshot({ path: S(`${src.src}-${v.label}-login`), fullPage: true })
        const lw = await overflowInfo(page)
        console.log(`F2 ${TAG} ${src.src} ${v.label} login scrollW=${lw.scroll} clientW=${lw.client} ok=${lw.scroll <= lw.client + 1}`)
        expect(lw.scroll, `${src.src}/${v.label} login 无横滚`).toBeLessThanOrEqual(lw.client + 1)
        await ctx.close()
      }
    })
  })
}
