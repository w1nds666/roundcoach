// 6R21 首页战术舞台性能门禁：真实数据回放（rAF + 时间插值 + 三态控制场）帧时分布，落盘 docs/phase6r21-perf.json。
// 记录 p50/p95/p99 帧时、>16.7ms 与 >33.3ms 的 jank 次数/比例、持续平均/最低 fps，以及测试机 CPU/RAM。
// 目标：持续播放约 60fps（p95 帧时 <= 16.7ms；若机器/高刷不适合则记录量化债务，绝不掩饰）。
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = join(__dirname, '..', '..', '..')
const PERF_JSON = join(REPO_ROOT, 'docs', 'phase6r21-perf.json')
const SHOTS = 'tests/e2e/screenshots'
const WARMUP_MS = 2000
const SAMPLE_MS = 4000

function percentile(sorted, p) {
  if (!sorted.length) return 0
  const idx = Math.min(sorted.length - 1, Math.floor(p * sorted.length))
  return sorted[idx]
}

test('6R21 首页战术舞台（真实回合回放）帧时分布 + 实测落盘', async ({ page }) => {
  await page.goto('/welcome')
  await expect(page.getByTestId('landing-replay-stage')).toBeVisible()
  await page.waitForSelector('.actor', { timeout: 15000 })
  await expect.poll(async () => page.getByTestId('landing-control-field').evaluate((canvas) => {
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
    let visible = 0
    for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 0) visible++
    return visible
  })).toBeGreaterThan(100)
  const navAreaCount = await page.evaluate(async () => {
    const response = await fetch('/api/maps/de_mirage/navmesh')
    if (!response.ok) throw new Error(`navmesh ${response.status}`)
    const nav = await response.json()
    return nav.areas.length
  })
  // 点击播放进入持续回放（rAF + 时间插值驱动）
  await page.getByRole('button', { name: '播放界面预览' }).click()
  await expect(page.getByRole('button', { name: '暂停界面预览' })).toBeVisible()
  await page.waitForTimeout(WARMUP_MS)

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
        const p = (q) => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] : 0)
        const over167 = gaps.filter((g) => g > 16.7).length
        const over333 = gaps.filter((g) => g > 33.3).length
        const avgGap = gaps.reduce((a, b) => a + b, 0) / (gaps.length || 1)
        resolve({
          frames: ts.length, ms: (ts[ts.length - 1] - start),
          avg_gap_ms: Number(avgGap.toFixed(3)), p50_ms: Number(p(0.5).toFixed(3)),
          p95_ms: Number(p(0.95).toFixed(3)), p99_ms: Number(p(0.99).toFixed(3)),
          max_gap_ms: Number(Math.max(...gaps).toFixed(3)),
          over167_count: over167, over167_ratio: Number((over167 / (gaps.length || 1)).toFixed(4)),
          over333_count: over333, over333_ratio: Number((over333 / (gaps.length || 1)).toFixed(4)),
          avg_fps: Number(((ts.length - 1) / ((ts[ts.length - 1] - start) / 1000)).toFixed(1)),
          min_fps: Number((1000 / (Math.max(...gaps) || 1000)).toFixed(1)),
        })
      }
    }
    requestAnimationFrame(loop)
  }), SAMPLE_MS)
  // 暂停
  await page.getByRole('button', { name: '暂停界面预览' }).click()

  const out = {
    test_machine: {
      cpu: 'AMD Ryzen 9 8940HX (32 threads)', ram_gb: 31.2, gpu: 'AMD Radeon(TM) 610M (integrated)',
      os: 'Windows 11 64-bit', browser: 'Chromium headless (Playwright)', viewport: '1440x900',
      note: '集成 GPU，非高刷显示器（60Hz 等效）。',
    },
    scene: { page: 'public landing /welcome', map: 'de_mirage', players: 10, navmesh_areas: navAreaCount, grid_n: 128,
      playback: 'rAF + 10Hz NavMesh scalar field Canvas + SVG zero-contour frontier', speed: 1 },
    warmup_ms: WARMUP_MS, sample_ms: SAMPLE_MS,
    sustained: res,
    gate: { target_p95_ms: 16.7, target_avg_fps: 55,
      note: '目标：持续播放 p95 帧时 <= 16.7ms（60fps 等效）；若机器/高刷不适合，用 60fps 等效帧时报告；未达则保留量化债务，绝不掩饰。' },
  }
  mkdirSync(join(REPO_ROOT, 'docs'), { recursive: true })
  writeFileSync(PERF_JSON, JSON.stringify(out, null, 2), 'utf8')
  console.log('perf_json_written=' + PERF_JSON)
  // 阶段快照
  const stage = page.locator('[data-testid="landing-replay-stage"]')
  await stage.screenshot({ path: `${SHOTS}/6r21-perf-stage.png` }).catch(() => {})

  // 断言：真实页面持续播放，最低帧率不低于 50fps；原始分位数全部落盘。
  expect(res.avg_fps).toBeGreaterThan(0)
  expect(res.min_fps).toBeGreaterThanOrEqual(50)
  console.log('6r21 perf avg_fps=' + res.avg_fps + ' p95_ms=' + res.p95_ms + ' jank_333=' + res.over333_count)
})
