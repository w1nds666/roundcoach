<script setup>
/**
 * 交互热力图 Canvas 组件（Phase 2.1 视觉升级前端侧）
 * - 密度网格 JSON（后端同源生成）→ 色阶 → 雷达轮廓 mask → 双线性放大渲染
 * - 控件真实生效：透明度（alpha 缩放）、半径（alpha 通道 box blur，扩散热区）
 * - CT/T 语义色（ct 蓝 / t 橙 / heat 通用热色）+ 强度图例
 * - PNG fallback（P1-2）：density 缺失时用带 Bearer 的 blob fetch 获取受保护 PNG，
 *   objectURL 展示并 revoke；绝不使用无认证 <img> 直链（避免 401 裂图）
 * - ready 状态（验收加固）：渲染/加载完成后设置 data-ready，父组件可等待并断言非空像素
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, required: true },
  mode: { type: String, required: true },        // kills_all | route_all | route_ct | route_t
  fallbackSrc: { type: String, default: '' },   // 受保护 PNG API 路径（blob 方式获取）
  densitySrc: { type: String, default: '' },    // 可选：个人事件密度 API（复用同一渲染器）
  opacity: { type: Number, default: 0.9 },      // 0-1 热力层不透明度
  radius: { type: Number, default: 0 },         // 0-6 额外扩散半径（网格单位）
})

const emit = defineEmits(['ready', 'error'])
const canvasRef = ref(null)
const imgRef = ref(null)
const err = ref('')
const mode_ = ref('canvas')   // canvas | png | error
const readyState = ref('loading')  // loading | ready | error（DOM 可等待的验收信号）
let density = null
let radarImg = null
let objectUrl = ''
let generation = 0

const SIZE = 1024
let gridSize = 256   // 从 density.buckets 动态读取（后端 GRID=512，Phase 6A）

const PALETTES = {
  heat: [[0, 20, 0, 90], [0.25, 66, 0, 255], [0.5, 0, 200, 255], [0.75, 255, 220, 0], [1, 255, 255, 255]],
  ct: [[0, 12, 30, 90], [0.4, 20, 90, 200], [0.75, 60, 170, 255], [1, 190, 240, 255]],
  t: [[0, 110, 30, 0], [0.4, 220, 90, 0], [0.75, 255, 160, 40], [1, 255, 235, 170]],
}
const PALETTE_FOR = { kills_all: 'heat', route_all: 'heat', route_ct: 'ct', route_t: 't' }

function lerpColor(stops, v) {
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, ...c0] = stops[i]
    const [t1, ...c1] = stops[i + 1]
    if (v <= t1) {
      const f = t1 === t0 ? 0 : (v - t0) / (t1 - t0)
      return [c0[0] + (c1[0] - c0[0]) * f, c0[1] + (c1[1] - c0[1]) * f, c0[2] + (c1[2] - c0[2]) * f]
    }
  }
  const last = stops[stops.length - 1]
  return [last[1], last[2], last[3]]
}

// alpha 通道 box blur（radius 控件真实作用：扩散热区）
function blurAlpha(img, radius) {
  if (radius <= 0) return
  const { width: w, height: h, data } = img
  const k = Math.max(1, Math.round(radius))
  const tmp = new Uint8ClampedArray(data.length)
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let sum = 0, n = 0
      for (let dx = -k; dx <= k; dx++) {
        const xx = x + dx
        if (xx >= 0 && xx < w) { sum += data[(y * w + xx) * 4 + 3]; n++ }
      }
      tmp[(y * w + x) * 4 + 3] = sum / n
    }
  }
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let sum = 0, n = 0
      for (let dy = -k; dy <= k; dy++) {
        const yy = y + dy
        if (yy >= 0 && yy < h) { sum += tmp[((yy * w) + x) * 4 + 3]; n++ }
      }
      data[(y * w + x) * 4 + 3] = sum / n
    }
  }
}

function render() {
  const canvas = canvasRef.value
  if (!canvas || !density) return
  const ctx = canvas.getContext('2d')
  const stops = PALETTES[PALETTE_FOR[props.mode] || 'heat']

  ctx.clearRect(0, 0, SIZE, SIZE)
  if (radarImg) ctx.drawImage(radarImg, 0, 0, SIZE, SIZE)

  const gridCanvas = document.createElement('canvas')
  gridCanvas.width = gridSize; gridCanvas.height = gridSize
  const gctx = gridCanvas.getContext('2d')
  const img = gctx.createImageData(gridSize, gridSize)

  let mask = null
  if (radarImg) {
    const mc = document.createElement('canvas')
    mc.width = gridSize; mc.height = gridSize
    const mctx = mc.getContext('2d')
    mctx.drawImage(radarImg, 0, 0, gridSize, gridSize)
    const mdata = mctx.getImageData(0, 0, gridSize, gridSize).data
    mask = new Float32Array(gridSize * gridSize)
    for (let i = 0; i < gridSize * gridSize; i++) mask[i] = mdata[i * 4 + 3] / 255
  }

  const cell = new Float32Array(gridSize * gridSize)
  for (const [x, y, v] of density.data) {
    cell[(gridSize - 1 - y) * gridSize + x] = Math.max(cell[(gridSize - 1 - y) * gridSize + x], v)
  }
  // 512 网格下 spread 展开 26 万参数会栈溢出：用循环求最大值（6R1 修复）
  let maxV = 0
  for (let i = 0; i < cell.length; i++) { if (cell[i] > maxV) maxV = cell[i] }
  for (let i = 0; i < gridSize * gridSize; i++) {
    const v = maxV > 0 ? cell[i] / maxV : 0
    if (v <= 0.002) { img.data[i * 4 + 3] = 0; continue }
    const [r, g, b] = lerpColor(stops, v)
    // alpha 曲线与后端一致（v^0.7 * 240）：低密度更透明、边缘更柔和（Phase 6A）
    let a = Math.pow(v, 0.7) * 240
    if (mask) a *= mask[i]
    a *= props.opacity
    img.data[i * 4] = r
    img.data[i * 4 + 1] = g
    img.data[i * 4 + 2] = b
    img.data[i * 4 + 3] = a
  }
  blurAlpha(img, props.radius)
  gctx.putImageData(img, 0, 0)

  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(gridCanvas, 0, 0, SIZE, SIZE)

  // 图例：左下角渐变条 + 低/高标注（Phase 6A 成品化）
  drawLegend(ctx, stops)
  readyState.value = 'ready'
  emit('ready')
}

function drawLegend(ctx, stops) {
  const lx = 24, ly = SIZE - 34, lw = 120, lh = 10
  const grad = ctx.createLinearGradient(lx, 0, lx + lw, 0)
  for (const [t, r, g, b] of stops) grad.addColorStop(t, `rgb(${r},${g},${b})`)
  ctx.globalAlpha = 0.92
  ctx.fillStyle = 'rgba(10,14,20,0.55)'
  ctx.fillRect(lx - 6, ly - 5, lw + 12, lh + 16)
  ctx.fillStyle = grad
  ctx.fillRect(lx, ly, lw, lh)
  ctx.globalAlpha = 1
  ctx.fillStyle = 'rgba(220,228,238,0.9)'
  ctx.font = '11px system-ui, sans-serif'
  ctx.textBaseline = 'middle'
  ctx.fillText('低', lx - 4, ly + lh + 12)
  ctx.fillText('高', lx + lw - 8, ly + lh + 12)
}

function clearObjectUrl() {
  if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = '' }
}

async function loadPngFallback() {
  clearObjectUrl()
  if (!props.fallbackSrc) {
    mode_.value = 'error'
    readyState.value = 'error'
    err.value = '热力图数据缺失'
    emit('error', err.value)
    return
  }
  try {
    const blob = await api.getBlob(props.fallbackSrc)
    objectUrl = URL.createObjectURL(blob)
    // 模板 img 通过 @load/@error 事件驱动 ready（避免渲染时序问题）
    mode_.value = 'png'
  } catch (e) {
    mode_.value = 'error'
    readyState.value = 'error'
    err.value = e.message || '热力图加载失败'
    emit('error', err.value)
  }
}

function onPngLoad() {
  const img = imgRef.value
  if (img && img.naturalWidth > 0) {
    readyState.value = 'ready'
    emit('ready')
  } else {
    mode_.value = 'error'
    readyState.value = 'error'
    err.value = '图片加载失败'
    emit('error', err.value)
  }
}

function onPngError() {
  mode_.value = 'error'
  readyState.value = 'error'
  err.value = '图片加载失败'
  emit('error', err.value)
}

async function load() {
  err.value = ''
  mode_.value = 'canvas'
  readyState.value = 'loading'
  // P2：任何 load 开始统一释放旧 objectURL，避免 PNG→Canvas 切换后泄漏
  clearObjectUrl()
  // P2：generation 递增，快速切换模式时旧请求不得覆盖新模式的渲染
  const gen = ++generation
  try {
    density = await api.get(props.densitySrc || `/api/demos/${props.demoId}/density/${props.mode}`)
    if (gen !== generation) return   // 已被更新的 load 取代
    if (density.buckets && density.buckets >= 16) gridSize = density.buckets   // 后端 GRID 动态对齐
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.src = `/api/maps/${props.mapName}/radar`
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej })
    if (gen !== generation) return
    radarImg = img
    mode_.value = 'canvas'
    render()
  } catch (e) {
    if (gen !== generation) return
    // density 缺失/失败 → 受保护 PNG fallback（blob + objectURL，不裂图）
    density = null
    await loadPngFallback()
  }
}

watch(() => props.mode, () => { load() })
watch(() => props.densitySrc, () => { load() })
watch(() => [props.opacity, props.radius], () => { if (mode_.value === 'canvas') render() })

onMounted(() => { load() })
onBeforeUnmount(() => { clearObjectUrl() })
</script>

<template>
  <div class="hm" :data-ready="readyState">
    <div v-if="mode_ === 'error'" class="form-error-box">{{ err }}</div>
    <canvas v-show="mode_ === 'canvas'" ref="canvasRef" :width="SIZE" :height="SIZE" class="hm-canvas" data-mode="canvas"></canvas>
    <img v-if="mode_ === 'png'" ref="imgRef" :src="objectUrl" class="hm-img" alt="热力图（静态版）" data-mode="png" @load="onPngLoad" @error="onPngError" />
  </div>
</template>

<style scoped>
.hm { position: relative; }
.hm-canvas { width: 100%; max-width: 640px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-base); }
.hm-img { width: 100%; max-width: 640px; border-radius: 10px; border: 1px solid var(--border); }
</style>
