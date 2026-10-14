<script setup>
/**
 * 6R7A：个人击杀/死亡图。默认「点位」视图（雷达底图 + 真实事件标记），
 * 提供「点位 / 密度」切换；密度才显示热力渐变与低/高图例。
 * 击杀=蓝色实心/小菱形（攻击者位置），死亡=清晰红色 X（受害者位置）；图例与画布一一对应。
 * 坐标/完整性只进默认收起的诊断明细；服务端权威 rx/ry。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '../api/client'
import HeatmapCanvas from './HeatmapCanvas.vue'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, required: true },
  viewType: { type: String, default: 'kills' },   // kills | deaths
  side: { type: String, default: 'all' },          // all | CT | T
})

const SIZE = 720
const COLOR_KILL = '#5b9dd9'   // 击杀：蓝/青
const COLOR_DEATH = '#e5645d'  // 死亡：清晰红 X
const canvasRef = ref(null)
const radarImg = ref(null)
const mode = ref('points')      // points | density
const combat = ref(null)
const loading = ref(false)
const error = ref('')
let gen = 0

const shown = computed(() => (props.viewType === 'kills' ? (combat.value?.kills || []) : (combat.value?.deaths || [])))

async function loadRadar() {
  const img = new Image(); img.crossOrigin = 'anonymous'; img.src = `/api/maps/${props.mapName}/radar`
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej })
  radarImg.value = img; scheduleRender()
}
async function loadCombat() {
  const g = ++gen
  loading.value = true; error.value = ''
  try {
    const data = await api.get(`/api/demos/${props.demoId}/personal/combat-map?side=${props.side}`)
    if (g !== gen) return
    combat.value = data
  } catch (e) { if (g === gen) error.value = e instanceof ApiError ? e.message : '个人点位加载失败' }
  finally { if (g === gen) { loading.value = false; scheduleRender() } }
}

function drawDot(ctx, rx, ry, r, fill, stroke) { ctx.beginPath(); ctx.arc(rx * SIZE, ry * SIZE, r, 0, Math.PI * 2); ctx.fillStyle = fill; ctx.fill(); if (stroke) { ctx.lineWidth = 1.5; ctx.strokeStyle = stroke; ctx.stroke() } }
function drawDiamond(ctx, rx, ry, size, color) { const x = rx * SIZE, y = ry * SIZE; ctx.beginPath(); ctx.moveTo(x, y - size); ctx.lineTo(x + size, y); ctx.lineTo(x, y + size); ctx.lineTo(x - size, y); ctx.closePath(); ctx.fillStyle = color; ctx.fill() }
function drawX(ctx, rx, ry, size, color) { ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.lineCap = 'round'; const x = rx * SIZE, y = ry * SIZE; ctx.beginPath(); ctx.moveTo(x - size, y - size); ctx.lineTo(x + size, y + size); ctx.moveTo(x + size, y - size); ctx.lineTo(x - size, y + size); ctx.stroke() }

function render() {
  const canvas = canvasRef.value; if (!canvas) return
  const ctx = canvas.getContext('2d')
  ctx.clearRect(0, 0, SIZE, SIZE)
  if (radarImg.value) ctx.drawImage(radarImg.value, 0, 0, SIZE, SIZE)
  // 重叠事件：按像素位置聚合计数（>1 显示计数徽标）
  const buckets = new Map()
  for (const p of shown.value) {
    if (p.rx == null || p.ry == null) continue
    const px = Math.round(p.rx * SIZE), py = Math.round(p.ry * SIZE)
    const k = `${px},${py}`
    buckets.set(k, (buckets.get(k) || []).concat(p))
  }
  for (const group of buckets.values()) {
    const p = group[0]
    if (props.viewType === 'kills') drawDiamond(ctx, p.rx, p.ry, 5, COLOR_KILL)
    else drawX(ctx, p.rx, p.ry, 7, COLOR_DEATH)
    if (group.length > 1) {
      // 聚合计数徽标
      const bx = p.rx * SIZE + 8, by = p.ry * SIZE - 10
      ctx.fillStyle = 'rgba(11,15,20,0.9)'; ctx.fillRect(bx, by, 13, 15)
      ctx.fillStyle = '#e7ecf2'; ctx.font = 'bold 11px system-ui, sans-serif'; ctx.textBaseline = 'top'
      ctx.fillText(String(group.length), bx + 3, by + 1)
    }
  }
}

let renderPending = false
function scheduleRender(retries = 0) {
  if (renderPending) return; renderPending = true
  nextTick(() => {
    renderPending = false
    if (canvasRef.value) { render(); return }
    if (retries < 40) scheduleRender(retries + 1)
  })
}

watch([radarImg, combat, () => props.viewType], () => scheduleRender())
// v-if recreates the canvas after density mode; redraw its new backing bitmap.
watch(mode, () => { if (mode.value === 'points') scheduleRender() })
watch(() => props.side, () => { if (props.mapName) loadCombat() })
onMounted(async () => { if (props.mapName) { try { await loadRadar() } catch { /* 忽略 */ } await loadCombat() } })
onBeforeUnmount(() => {})
</script>

<template>
  <div class="pcm">
    <div class="pcm-controls">
      <div class="seg" role="group" aria-label="视图">
        <button class="seg-btn" :class="{ on: mode === 'points' }" @click="mode = 'points'">点位</button>
        <button class="seg-btn" :class="{ on: mode === 'density' }" @click="mode = 'density'">密度</button>
      </div>
    </div>

    <div v-if="loading" class="pcm-empty">加载中…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else>
      <!-- 文本摘要：与当前视图/图例一致（点位=计数，密度=热力样本） -->
      <p class="pcm-summary" v-if="mode === 'points'">
        {{ shown.length }} 个{{ viewType === 'kills' ? '击杀' : '死亡' }}点位
        <span class="pcm-summary-sub">（{{ viewType === 'kills' ? '攻击者位置' : '受害者位置' }}）</span>
      </p>
      <p class="pcm-summary" v-else>
        {{ viewType === 'kills' ? '击杀' : '死亡' }}密度热力图
        <span class="pcm-summary-sub">（基于 {{ shown.length }} 个样本）</span>
      </p>

      <!-- 点位视图：真实事件标记，图例与画布一致 -->
      <div v-if="mode === 'points'" class="pcm-stage">
        <canvas ref="canvasRef" :width="SIZE" :height="SIZE" class="pcm-canvas" data-ready="ready"></canvas>
        <div class="pcm-legend">
          <span class="lg" v-if="viewType === 'kills'"><i class="dot" :style="{ background: COLOR_KILL }"></i>击杀（攻击者位置）</span>
          <span class="lg" v-else><i class="x">✕</i>死亡（受害者位置）</span>
        </div>
        <div v-if="shown.length === 0" class="pcm-empty">该筛选下没有{{ viewType === 'kills' ? '击杀' : '死亡' }}点位</div>
      </div>

      <!-- 密度视图：热力渐变 + 低/高图例（复用 HeatmapCanvas） -->
      <div v-else-if="mode === 'density'" class="pcm-stage">
        <HeatmapCanvas
          :demo-id="demoId" :map-name="mapName" mode="kills_all"
          :density-src="`/api/demos/${demoId}/personal/combat-density?kind=${viewType}&side=${side}`" fallback-src="" />
      </div>

      <details class="pcm-diag">
        <summary>技术诊断 / 导出</summary>
        <p class="pcm-diag-line">共 {{ shown.length }} 个{{ viewType === 'kills' ? '击杀' : '死亡' }}点位（去重前）。世界坐标见下表。</p>
        <div class="table-scroll"><table class="tbl"><thead><tr><th>回合</th><th>阵营</th><th>世界坐标</th><th>武器</th></tr></thead>
          <tbody><tr v-for="(p, i) in shown" :key="i"><td class="num">R{{ p.round }}</td><td>{{ p.side || '—' }}</td>
            <td class="num muted">{{ p.x !== null ? `(${Math.round(p.x)}, ${Math.round(p.y)})` : '—' }}</td>
            <td>{{ p.weapon || '—' }}</td></tr></tbody></table>
        </div>
      </details>
    </template>
  </div>
</template>

<style scoped>
.pcm { display: flex; flex-direction: column; gap: 12px; }
.pcm-controls { display: flex; }
.seg { display: flex; gap: 6px; }
.seg-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 5px 14px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; }
.seg-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.pcm-empty { color: var(--text-muted); text-align: center; padding: 24px; font-size: 13px; }
.pcm-summary { font-size: 12.5px; color: var(--text-secondary); }
.pcm-summary-sub { color: var(--text-muted); }
.pcm-stage { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; }
.pcm-canvas { width: 100%; max-width: 640px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-base); aspect-ratio: 1; }
.pcm-legend { display: flex; gap: 18px; font-size: 12.5px; color: var(--text-secondary); }
.pcm-legend .lg { display: inline-flex; align-items: center; gap: 6px; }
.pcm-legend .dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; transform: rotate(45deg); }
.pcm-legend .x { color: var(--err); font-weight: 700; font-style: normal; }
.pcm-diag { border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elevated); padding: 8px 12px; font-size: 12px; }
.pcm-diag summary { cursor: pointer; color: var(--text-secondary); }
.pcm-diag-line { color: var(--text-muted); font-size: 11.5px; margin: 8px 0 4px; }
</style>
