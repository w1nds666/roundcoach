<script setup>
/**
 * 6R7B：团队击杀/死亡图（Max 交战面，与个人交战面数据范围隔离）。
 * - 拉取 /api/demos/{id}/team/combat-map?team={team}&side={side}
 * - 数据范围独立：团队含全部玩家，个人仅 focus 玩家（后端已隔离，前端不混用）
 * - 筛选：team（全部/队伍A/队伍B）+ side（全部/CT/T）+ 视图（击杀/死亡）+ 点位/密度
 * - 标记语义与个人交战图一致（kills=蓝色菱形攻击者位置，deaths=红色 X 受害者位置），图例一一对应
 * - 密度复用 /team/combat-density（后端已提供）；模式/图例/画布/摘要保持一致，绝不伪造。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '../api/client'
import HeatmapCanvas from './HeatmapCanvas.vue'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, required: true },
})

const SIZE = 720
const COLOR_KILL = '#5b9dd9'   // 击杀：蓝/青（攻击者位置）
const COLOR_DEATH = '#e5645d'  // 死亡：清晰红 X（受害者位置）
const canvasRef = ref(null)
const radarImg = ref(null)
const combat = ref(null)
const loading = ref(false)
const error = ref('')
const team = ref('all')          // all | A | B（队伍身份）
const viewType = ref('kills')    // kills | deaths
const side = ref('all')          // all | CT | T（该队该回合真实阵营）
const mode = ref('points')       // points | density（密度当前不可用，仅点位可交互）
let gen = 0

const shown = computed(() => (viewType.value === 'kills' ? (combat.value?.kills || []) : (combat.value?.deaths || [])))
const teamFilterLabel = computed(() => (team.value === 'A' ? '队伍A' : team.value === 'B' ? '队伍B' : '全部'))
const sideFilterLabel = computed(() => (side.value === 'CT' ? 'CT' : side.value === 'T' ? 'T' : '全部'))

async function loadRadar() {
  const img = new Image(); img.crossOrigin = 'anonymous'; img.src = `/api/maps/${props.mapName}/radar`
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej })
  radarImg.value = img; scheduleRender()
}
async function loadCombat() {
  const g = ++gen
  loading.value = true; error.value = ''
  try {
    const data = await api.get(`/api/demos/${props.demoId}/team/combat-map?team=${team.value}&side=${side.value}`)
    if (g !== gen) return
    combat.value = data
  } catch (e) { if (g === gen) error.value = e instanceof ApiError ? e.message : '团队点位加载失败' }
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
    if (viewType.value === 'kills') drawDiamond(ctx, p.rx, p.ry, 5, COLOR_KILL)
    else drawX(ctx, p.rx, p.ry, 7, COLOR_DEATH)
    if (group.length > 1) {
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

watch([radarImg, combat, viewType], () => scheduleRender())
watch([team, side], () => { if (props.mapName) loadCombat() })
watch(() => props.mapName, () => { if (props.mapName) { loadRadar().catch(() => {}); loadCombat() } })
onMounted(async () => { if (props.mapName) { try { await loadRadar() } catch { /* 忽略 */ } await loadCombat() } })
onBeforeUnmount(() => {})
</script>

<template>
  <div class="tcm">
    <div class="tcm-controls">
      <div class="seg" role="group" aria-label="队伍筛选">
        <button v-for="t in [['all', '全部'], ['A', '队伍A'], ['B', '队伍B']]" :key="t[0]" class="seg-btn"
          :class="{ on: team === t[0] }" @click="team = t[0]">{{ t[1] }}</button>
      </div>
      <div class="seg" role="group" aria-label="视图">
        <button class="seg-btn" :class="{ on: viewType === 'kills' }" @click="viewType = 'kills'">击杀</button>
        <button class="seg-btn" :class="{ on: viewType === 'deaths' }" @click="viewType = 'deaths'">死亡</button>
      </div>
      <div class="seg" role="group" aria-label="阵营筛选">
        <button v-for="s in [['all', '全部'], ['CT', 'CT'], ['T', 'T']]" :key="s[0]" class="seg-btn"
          :class="{ on: side === s[0] }" @click="side = s[0]">{{ s[1] }}</button>
      </div>
      <div class="seg" role="group" aria-label="视图模式">
        <button class="seg-btn" :class="{ on: mode === 'points' }" @click="mode = 'points'">点位</button>
        <button class="seg-btn" :class="{ on: mode === 'density' }" @click="mode = 'density'">密度</button>
      </div>
    </div>

    <div v-if="loading" class="tcm-empty">加载中…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else>
      <p class="tcm-summary">
        <template v-if="mode === 'points'">{{ shown.length }} 个{{ viewType === 'kills' ? '击杀' : '死亡' }}点位（{{ viewType === 'kills' ? '攻击者位置' : '受害者位置' }} · 队伍：{{ teamFilterLabel }} · 阵营：{{ sideFilterLabel }}）</template>
        <template v-else>{{ viewType === 'kills' ? '击杀' : '死亡' }}密度热力图（基于 {{ shown.length }} 个样本 · 队伍：{{ teamFilterLabel }} · 阵营：{{ sideFilterLabel }}）</template>
      </p>

      <div v-if="mode === 'points'" class="tcm-stage">
        <canvas ref="canvasRef" :width="SIZE" :height="SIZE" class="tcm-canvas" data-ready="ready"></canvas>
        <div class="tcm-legend">
          <span class="lg" v-if="viewType === 'kills'"><i class="dot" :style="{ background: COLOR_KILL }"></i>击杀（攻击者位置）</span>
          <span class="lg" v-else><i class="x">✕</i>死亡（受害者位置）</span>
        </div>
        <div v-if="shown.length === 0" class="tcm-empty">该筛选下没有{{ viewType === 'kills' ? '击杀' : '死亡' }}点位</div>
      </div>

      <div v-else class="tcm-stage">
        <HeatmapCanvas
          :demo-id="demoId" :map-name="mapName" mode="kills_all"
          :density-src="`/api/demos/${demoId}/team/combat-density?kind=${viewType}&team=${team}&side=${side}`" fallback-src="" />
        <div class="tcm-legend"><span class="lg"><i class="dot" :style="{ background: COLOR_KILL }"></i>击杀密度（攻击者）/ {{ viewType === 'kills' ? '击杀' : '死亡' }} 热度</span></div>
      </div>

      <details class="tcm-diag">
        <summary>技术诊断 / 导出</summary>
        <p class="tcm-diag-line">共 {{ shown.length }} 个{{ viewType === 'kills' ? '击杀' : '死亡' }}点位（去重前）。团队交战图与个人交战图为独立数据范围。</p>
        <div class="table-scroll"><table class="tbl"><thead><tr><th>回合</th><th>阵营</th><th>队伍</th><th>世界坐标</th><th>武器</th></tr></thead>
          <tbody><tr v-for="(p, i) in shown" :key="i"><td class="num">R{{ p.round }}</td><td>{{ p.side || '—' }}</td><td>{{ p.team || '—' }}</td>
            <td class="num muted">{{ p.x !== null ? `(${Math.round(p.x)}, ${Math.round(p.y)})` : '—' }}</td>
            <td>{{ p.weapon || '—' }}</td></tr></tbody></table>
        </div>
      </details>
    </template>
  </div>
</template>

<style scoped>
.tcm { display: flex; flex-direction: column; gap: 12px; }
.tcm-controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.seg { display: flex; gap: 4px; }
.seg-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 5px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; }
.seg-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.seg-btn:disabled { opacity: 0.55; cursor: not-allowed; }
.tcm-note { font-size: 12px; color: var(--text-muted); }
.tcm-empty { color: var(--text-muted); text-align: center; padding: 24px; font-size: 13px; }
.tcm-summary { font-size: 12.5px; color: var(--text-secondary); }
.tcm-summary-sub { color: var(--text-muted); }
.tcm-stage { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; }
.tcm-canvas { width: 100%; max-width: 640px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-base); aspect-ratio: 1; }
.tcm-legend { display: flex; gap: 18px; font-size: 12.5px; color: var(--text-secondary); }
.tcm-legend .lg { display: inline-flex; align-items: center; gap: 6px; }
.tcm-legend .dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; transform: rotate(45deg); }
.tcm-legend .x { color: var(--err); font-weight: 700; font-style: normal; }
.tcm-diag { border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elevated); padding: 8px 12px; font-size: 12px; }
.tcm-diag summary { cursor: pointer; color: var(--text-secondary); }
.tcm-diag-line { color: var(--text-muted); font-size: 11.5px; margin: 8px 0 4px; }
.table-scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: 12px; padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.tbl td { padding: 5px 8px; border-bottom: 1px solid rgba(35, 45, 61, 0.5); white-space: nowrap; }
.tbl tr:last-child td { border-bottom: none; }
.num { font-family: ui-monospace, monospace; }
.muted { color: var(--text-muted); }
</style>
