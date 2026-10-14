<script setup>
/**
 * 6R8：可解释深度配合原语（Max）—— 可定位到同步回放的候选列表。
 * - 数据源：/api/demos/{id}/tactical/primitives（无 round 过滤 → 全部回合）
 * - 按回合分组；每条 = kind(中文徽标) / R{n} / mm:ss / 中文区域 / 涉及玩家 / 定义 / 证据
 * - crossfire_candidate 额外展示 reason（无墙体遮挡验证）
 * - 点击条目或「定位到回放」→ emit locate({round, time})，父级驱动 TeamSyncReplay
 * - 不输出"深度配合总分"；不展示 raw tick / 世界坐标
 */
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '../api/client'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, default: '' },
  maxRound: { type: Number, default: 0 },
})

const emit = defineEmits(['locate'])

const KIND_LABEL = {
  trade_window: '交换',
  spacing: '间距',
  simultaneous_contact: '同步接敌',
  crossfire_candidate: '交叉火力候选',
  utility_assisted_entry: '道具辅助进点',
  isolated_death: '孤立阵亡',
  area_control: '区域控制',
}
// 语义配色（全部来自既有设计系统 soft 变体，克制不彩虹）
const KIND_TONE = {
  trade_window: 'info',
  spacing: 'info',
  simultaneous_contact: 'warn',
  crossfire_candidate: 'accent',
  utility_assisted_entry: 'ok',
  isolated_death: 'err',
  area_control: 'info',
}

const data = ref(null)
const loading = ref(false)
const error = ref('')

const primitives = computed(() => data.value?.primitives || [])
// 按回合分组（后端已按 round 排序）；每条带 R{n} 可单条定位
const groups = computed(() => {
  const map = new Map()
  for (const p of primitives.value) {
    if (!map.has(p.round)) map.set(p.round, [])
    map.get(p.round).push(p)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([round, items]) => ({ round, items }))
})

function fmtTime(sec) {
  const n = Number(sec)
  if (!Number.isFinite(n) || n < 0) return '—'
  const s = Math.floor(n); const m = Math.floor(s / 60); const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}
function kindLabel(k) { return KIND_LABEL[k] || k }
function toneClass(k) { return `pk-${KIND_TONE[k] || 'info'}` }

function locateEntry(item) {
  // time 为空（时长不可核算）时仍定位到该回合起点，保证可交互
  emit('locate', { round: item.round, time: item.time != null ? Number(item.time) : 0 })
}

async function load() {
  loading.value = true; error.value = ''
  try {
    data.value = await api.get(`/api/demos/${props.demoId}/tactical/primitives`)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '协同原语加载失败'
    data.value = null
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="cp">
    <div v-if="loading" class="cp-state">加载协同原语…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else>
      <div v-if="groups.length === 0" class="cp-state">暂无已识别的协同原语候选（本阶段只给候选，不产出配合总分）。</div>

      <div v-else class="cp-body">
        <div v-for="g in groups" :key="g.round" class="cp-round">
          <div class="cp-round-head">
            <span class="cp-round-no">R{{ g.round }}</span>
            <span class="cp-round-count num">{{ g.items.length }} 条</span>
          </div>
          <ul class="cp-list">
            <li v-for="(it, i) in g.items" :key="i" class="cp-item" :class="{ clickable: it.time != null }"
              @click="it.time != null && locateEntry(it)">
              <div class="cp-item-line">
                <span class="pk" :class="toneClass(it.kind)">{{ kindLabel(it.kind) }}</span>
                <span class="cp-time num">{{ fmtTime(it.time) }}</span>
                <span class="cp-region">{{ it.region || '未知区域' }}</span>
                <span v-if="it.player_names" class="cp-players">{{ it.player_names }}</span>
              </div>
              <p class="cp-def">{{ it.definition }}</p>
              <p class="cp-ev">证据：{{ it.evidence }}</p>
              <p v-if="it.kind === 'crossfire_candidate' && it.reason" class="cp-reason">说明：{{ it.reason }}</p>
              <div class="cp-goto">
                <button class="btn btn-ghost cp-locate" :disabled="it.time == null" @click.stop="locateEntry(it)">定位到回放</button>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cp { display: flex; flex-direction: column; gap: 12px; }
.cp-state { color: var(--text-muted); text-align: center; padding: 28px; font-size: 13px; }
.cp-body { display: flex; flex-direction: column; gap: 18px; }
.cp-round { display: flex; flex-direction: column; gap: 8px; }
.cp-round-head { display: flex; align-items: center; gap: 10px;
  border-bottom: 1px solid var(--border); padding-bottom: 6px; }
.cp-round-no { font-size: 13px; font-weight: 700; color: var(--text-primary); }
.cp-round-count { font-size: 12px; color: var(--text-muted); }
.cp-list { list-style: none; display: flex; flex-direction: column; gap: 8px; }
.cp-item { border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elevated);
  padding: 10px 12px; display: flex; flex-direction: column; gap: 5px; }
.cp-item.clickable { cursor: pointer; }
.cp-item.clickable:hover { border-color: var(--accent); }
.cp-item-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pk { display: inline-flex; align-items: center; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.pk-info { color: var(--info); background: var(--info-soft); }
.pk-warn { color: var(--warn); background: var(--warn-soft); }
.pk-accent { color: var(--accent); background: var(--accent-soft); }
.pk-ok { color: var(--ok); background: var(--ok-soft); }
.pk-err { color: var(--err); background: var(--err-soft); }
.cp-time { font-size: 12.5px; color: var(--text-secondary); font-weight: 600; }
.cp-region { font-size: 12.5px; color: var(--text-secondary); }
.cp-players { font-size: 12.5px; color: var(--text-primary); font-weight: 600; }
.cp-def { font-size: 12.5px; color: var(--text-primary); }
.cp-ev { font-size: 12px; color: var(--text-muted); }
.cp-reason { font-size: 12px; color: var(--warn); }
.cp-goto { margin-top: 2px; display: flex; }
.cp-locate { font-size: 12px; padding: 3px 10px; }
.cp-locate:disabled { opacity: 0.45; }
</style>
