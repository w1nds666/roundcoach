<script setup>
// 首页「回合证据」缩略图：复用产品的 NavMesh/scalar-field 真值管线，
// 只渲染一次真实证据快照，不创建逐 texel DOM，也不把色块冒充区域控制。
import { computed, onMounted, ref } from 'vue'
import { createPipeline } from '../map-control/pipeline.js'
import { renderScalarField } from '../map-control/fieldTexture.js'

const props = defineProps({
  sample: { type: Object, required: true },
})

const GRID_N = 128
const FIELD_CFG = Object.freeze({
  sigma: 550,
  neutralThreshold: 0.22,
  contestRatio: 0.2,
  maxPropagationDistance: 2200,
  frontierBand: 0.05,
})

const canvas = ref(null)
const status = ref('loading')
const players = ref([])
const frontierPaths = ref({ active: [], low: [] })

const evidence = computed(() => (
  props.sample?.evidence?.find((item) => item.kind === 'control' && item.state === 'contested')
  || props.sample?.evidence?.find((item) => item.kind === 'control')
  || null
))
const previewTime = computed(() => Number(evidence.value?.time) || 0)
const evidenceArea = computed(() => (
  props.sample?.areas?.find((area) => area.place_key === evidence.value?.place_key) || null
))
const mapName = computed(() => props.sample?.map?.name || 'de_mirage')
const mapLabel = computed(() => mapName.value.replace(/^de_/, '').replace(/^./, (letter) => letter.toUpperCase()))
const timeLabel = computed(() => {
  const seconds = Math.max(0, Math.floor(previewTime.value))
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
})
const description = computed(() => (
  `${mapLabel.value} 第 ${props.sample?.round?.number || 0} 回合，${timeLabel.value}，${evidence.value?.title || '区域控制证据'}。颜色来自官方 NavMesh 上的存活玩家控制场。`
))

function contourPath(line) {
  if (!line?.length) return ''
  return line.map((point, index) => `${index ? 'L' : 'M'}${point[0].toFixed(2)} ${point[1].toFixed(2)}`).join(' ')
}

// 缩略图将完整雷达等比放大并裁到 B 区；所有覆盖层使用同一投影，避免坐标漂移。
function previewPointStyle(x, y) {
  return {
    left: `${(-0.04 + Number(x) * 1.08) * 100}%`,
    top: `${(-0.15 + Number(y) * 1.3) * 100}%`,
  }
}

async function renderPreview() {
  status.value = 'loading'
  try {
    const response = await fetch(`/api/maps/${encodeURIComponent(mapName.value)}/navmesh`)
    if (!response.ok) throw new Error(`navmesh ${response.status}`)
    const nav = await response.json()
    if (!Array.isArray(nav?.areas) || !nav.areas.length) throw new Error('navmesh empty')

    const pipeline = createPipeline({
      nav,
      sample: props.sample,
      cfg: FIELD_CFG,
      opts: { gridN: GRID_N, cacheLimit: 4 },
    })
    const contour = pipeline.contourAt(previewTime.value)
    const ctx = canvas.value?.getContext('2d')
    if (!ctx) throw new Error('canvas unavailable')
    renderScalarField(ctx, GRID_N, { cv: contour.scv, mag: contour.grid.mag, mask: contour.grid.mask }, FIELD_CFG, 0)

    const live = pipeline.playersAt(previewTime.value).filter((player) => player.alive)
    players.value = live.map((player) => {
      const radar = pipeline.toRadar(player.wx, player.wy)
      return { ...player, x: radar.rx, y: radar.ry, num: player.uid.replace(/^C?T/, '') }
    })
    const hasCt = live.some((player) => player.side === 'CT')
    const hasT = live.some((player) => player.side === 'T')
    frontierPaths.value = {
      active: hasCt && hasT ? contour.activeLines.map(contourPath).filter(Boolean) : [],
      low: hasCt && hasT ? contour.lowLines.map(contourPath).filter(Boolean) : [],
    }
    status.value = 'ready'
  } catch (error) {
    players.value = []
    frontierPaths.value = { active: [], low: [] }
    status.value = 'unavailable'
  }
}

onMounted(renderPreview)
</script>

<template>
  <figure
    class="control-preview"
    :aria-label="description"
    data-testid="proof-control-preview"
  >
    <div class="control-preview-map">
      <img :src="`/maps/${mapName}.png`" :alt="`${mapLabel} 官方雷达结构底图`" />
      <canvas
        ref="canvas"
        :width="GRID_N"
        :height="GRID_N"
        data-testid="proof-control-field"
        aria-hidden="true"
      ></canvas>
      <svg :viewBox="`0 0 ${GRID_N} ${GRID_N}`" preserveAspectRatio="none" aria-hidden="true">
        <path v-for="(path, index) in frontierPaths.active" :key="`active-${index}`" :d="path" class="frontier active" />
        <path v-for="(path, index) in frontierPaths.low" :key="`low-${index}`" :d="path" class="frontier low" />
      </svg>
      <span
        v-for="player in players"
        :key="player.uid"
        class="preview-player"
        :class="player.side === 'T' ? 't' : 'ct'"
        :style="previewPointStyle(player.x, player.y)"
        aria-hidden="true"
      >{{ player.num }}</span>
      <span
        v-if="evidenceArea?.centroid"
        class="preview-area"
        :style="previewPointStyle(evidenceArea.centroid.rx, evidenceArea.centroid.ry)"
        aria-hidden="true"
      >{{ evidence?.place_cn || evidenceArea.place_cn }} · {{ evidence?.state === 'contested' ? '争夺' : '控制证据' }}</span>
      <span v-if="status !== 'ready'" class="preview-status">{{ status === 'loading' ? '正在装载 NavMesh 真值' : 'NavMesh 真值暂不可用' }}</span>
    </div>
    <figcaption>
      <span>真实第 {{ sample.round.number }} 回合 · {{ timeLabel }}</span>
      <span class="preview-legend"><i class="t"></i>T <i class="contested"></i>争夺 <i class="ct"></i>CT</span>
    </figcaption>
  </figure>
</template>

<style scoped>
.control-preview { margin: 0; min-width: 0; border: 1px solid var(--border); background: #080c11; overflow: hidden; }
.control-preview-map { position: relative; aspect-ratio: 1; overflow: hidden; isolation: isolate; }
.control-preview-map > img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; filter: grayscale(.18) brightness(.82) contrast(1.06); z-index: 0; }
.control-preview-map > canvas { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 1; pointer-events: none; }
.control-preview-map > svg { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 2; pointer-events: none; }
.frontier { fill: none; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.frontier.active { stroke: rgba(140,220,255,.98); stroke-width: 2.2; filter: drop-shadow(0 0 3px rgba(91,157,217,.68)); }
.frontier.low { stroke: rgba(220,226,235,.72); stroke-width: 1.5; stroke-dasharray: 5 4; }
.preview-player { position: absolute; width: 17px; height: 17px; display: grid; place-items: center; transform: translate(-50%,-50%); border: 2px solid #f0f4f8; border-radius: 50%; font-size: 8px; font-weight: 900; z-index: 4; box-shadow: 0 1px 7px rgba(0,0,0,.8); }
.preview-player.ct { color: #07111a; background: #5b9dd9; }
.preview-player.t { color: #17110a; background: #e6a54a; }
.preview-area { position: absolute; transform: translate(-50%,-50%); padding: 3px 7px; border: 1px solid rgba(176,148,238,.62); border-radius: 4px; color: #ded2ff; background: rgba(8,12,16,.88); font-size: 9px; font-weight: 800; white-space: nowrap; z-index: 5; }
.preview-status { position: absolute; inset: 0; display: grid; place-items: center; color: var(--text-muted); background: rgba(8,12,16,.68); font-size: 10px; z-index: 6; }
figcaption { min-height: 30px; padding: 0 10px; display: flex; align-items: center; justify-content: space-between; gap: 10px; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 9px; }
.preview-legend { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.preview-legend i { width: 6px; height: 6px; border-radius: 50%; }
.preview-legend .t { background: var(--accent); }
.preview-legend .ct { background: var(--info); }
.preview-legend .contested { background: #b094ee; }

</style>
