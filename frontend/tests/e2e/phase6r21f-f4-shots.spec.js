// 6R21F-F4 视觉证据矩阵：Coach / Profile / Share，桌面与 390/360。
import { test } from '@playwright/test'
import { login, ACCOUNT, setTier, seedIdentity, demoPlayerSteam, coachWarm } from './_helpers6r15.js'

const OUT = '../docs/visual-evidence'

async function shot(page, path, selector, name) {
  await page.goto(path)
  await page.waitForSelector(selector, { timeout: 20000 })
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${OUT}/6r21f-f4-${name}.png`, fullPage: true })
}

test('F4 after 截图矩阵', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, 19))
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await coachWarm(page, 10)

  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const response = await page.request.post('/api/demos/19/share', {
    headers: { Authorization: `Bearer ${token}` },
    data: { expires_in_hours: 24 },
  })
  const shareToken = (await response.json()).token

  for (const [width, height, tag] of [
    [1440, 900, 'desktop1440'],
    [1280, 900, 'desktop1280'],
    [1081, 900, 'desktop1081'],
    [900, 900, 'desktop900'],
    [390, 844, 'mobile390'],
    [360, 780, 'mobile360'],
  ]) {
    await page.setViewportSize({ width, height })
    await shot(page, '/coach', '.coach-command', `coach-after-${tag}`)
    await shot(page, '/profile', '.identity-command', `profile-after-${tag}`)
    await shot(page, `/share#${shareToken}`, '.share-trustbar', `share-after-${tag}`)
  }
})
