// 6R21B regression probe: 360px 无横滚，含「长名/徽标（这是你）」在最窄视口不撑破报告头部。
// 绑定长的玩家名（demo21 players[0] = 七侠镇第37任缁衣老捕头邢育森）到「这是你」徽标，验证 .head 不横滚。
import { test, expect } from '@playwright/test'
import { seedIdentity, demoPlayerSteam, grantTeamEntitlementForDemo } from './_helpers6r14.js'
import { login, ACCOUNT, setTier } from './_helpers6r15.js'

test('dust2-360 report head no horizontal overflow with long identity name', async ({ browser }) => {
  setTier(ACCOUNT.email, 'max', 30)
  const id = 21
  seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, id))
  grantTeamEntitlementForDemo(ACCOUNT.email, id)
  const ctx = await browser.newContext({ viewport: { width: 360, height: 800 } })
  const p = await ctx.newPage()
  await login(p, ACCOUNT.email, ACCOUNT.password)
  await p.goto(`/demo/${id}`)
  await p.waitForSelector('.head', { timeout: 30000 }).catch(() => {})
  await p.waitForTimeout(900)
  const m = await p.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth, body: document.body.scrollWidth }))
  console.log('META', JSON.stringify(m))
  expect(m.scroll).toBeLessThanOrEqual(m.client + 1)
  expect(m.body).toBeLessThanOrEqual(m.client + 1)
  const head = await p.locator('.head').boundingBox()
  expect(head).toBeTruthy()
  expect(head.x + head.width).toBeLessThanOrEqual(361)
})
