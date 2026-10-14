<script setup>
/**
 * 6R1：「我的」个人分析 Tab
 * - 玩家选择器（opaque player_id，服务端 entitlement 驱动锁定）
 * - 个人摘要（K/D、交战转化率 K/(K+D) 含公式、ADR、首杀/首死、存活回合）
 * - 个人交战图：kills（蓝）/deaths（红）分离散点，CT/T 过滤
 * - Free：可选择「我」看基础摘要预览，完整坐标由服务端拒绝
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api, ApiError } from '../api/client'
import PersonalCombatMap from './PersonalCombatMap.vue'
import PersonalTacticalReplay from './PersonalTacticalReplay.vue'
import RatingPro from './RatingPro.vue'

const props = defineProps({ demoId: { type: [Number, String], required: true }, mapName: { type: String, default: '' }, locateRequest: { type: Object, default: null } })

const entitlement = ref(null)
const players = ref([])
const focus = ref(null)
const summary = ref(null)
const combat = ref(null)
const grenadesMeta = ref(null)
const loading = ref(false)
const error = ref('')
const sideFilter = ref('all')
const view = ref('kills')   // kills | deaths（分图开关）
const showDetail = ref(false)   // 6R5A：是否展开「数据明细」坐标表（切筛选/视图后保持展开）
let loadGeneration = 0

const tier = computed(() => entitlement.value?.tier || 'free')
const canPersonal = computed(() => entitlement.value?.features?.personal_analysis || false)

async function loadEntitlement() {
  try { entitlement.value = await api.get('/api/entitlement') } catch { /* 忽略 */ }
}

async function loadPlayers() {
  try {
    players.value = await api.get(`/api/demos/${props.demoId}/players`)
    focus.value = await api.get(`/api/demos/${props.demoId}/focus-player`)
    // Tab 使用 v-if，切走再回来会重新挂载；焦点从服务端恢复后必须主动重载个人数据。
    if (focus.value?.player_id && canPersonal.value) await loadPersonal()
    // 6R15：带证据定位进入时，把焦点锁定到该证据对应的玩家（体现「我」/highlight 玩家）
    applyLocatePlayer()
  } catch (e) { error.value = e.message }
}

// 6R15：长期教练证据跳转 → 把个人复盘焦点切到证据所属玩家（优先绑定「我」，其次 highlight 第一名）
function applyLocatePlayer() {
  const req = props.locateRequest
  if (req?.round == null) return
  const targetId = (youPlayer.value?.player_id ?? null)
    ?? (req.highlight?.[0] != null ? Number(req.highlight[0]) : null)
  if (targetId == null) return
  const inPlayers = players.value.some((p) => p.player_id === targetId)
  if (!inPlayers) return
  if (focus.value?.player_id && Number(focus.value.player_id) === targetId) return
  selectPlayer(targetId)
}

async function selectPlayer(playerId) {
  if (!entitlement.value) await loadEntitlement()   // 竞态防护：entitlement 先就绪
  try {
    await api.put(`/api/demos/${props.demoId}/focus-player`, { player_id: playerId })
    focus.value = { player_id: playerId }
    error.value = ''
    await loadPersonal()
  } catch (e) { error.value = e.message }
}

async function loadPersonal() {
  if (!focus.value?.player_id) return
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  try {
    const nextSummary = await api.get(`/api/demos/${props.demoId}/personal/summary`)
    const nextCombat = await api.get(`/api/demos/${props.demoId}/personal/combat-map?side=${sideFilter.value}`)
    const nextGrenades = await api.get(`/api/demos/${props.demoId}/grenades`)
    // 筛选快速切换时，旧请求不得覆盖当前焦点/筛选的数据。
    if (generation !== loadGeneration) return
    summary.value = nextSummary
    combat.value = nextCombat
    grenadesMeta.value = nextGrenades
  } catch (e) {
    // 服务端权威：Free/未授权 → 403（显示锁定）；其余错误展示
    error.value = e instanceof ApiError ? e.message : '加载失败'
  } finally { loading.value = false }
}

watch(sideFilter, () => { if (canPersonal.value) loadPersonal() })
watch(() => props.locateRequest, () => { if (props.locateRequest?.round != null) applyLocatePlayer() })

const selectedName = computed(() => {
  if (!focus.value?.player_id) return ''
  return players.value.find((p) => p.player_id === focus.value.player_id)?.name || ''
})

// 6R14：绑定身份自动识别「我」；用 is_you 区分他人视角（免费探索）
const youPlayer = computed(() => players.value.find((p) => p.is_you) || null)
const viewingOther = computed(() => !!youPlayer.value && !!focus.value?.player_id && focus.value.player_id !== youPlayer.value.player_id)

const shownPoints = computed(() => {
  const src = view.value === 'kills' ? combat.value?.kills : combat.value?.deaths
  return src || []
})

// 个人道具：只统计所选（focus）玩家；聚合计数；回合使用明细已降级到个人战术回放的技术诊断。
const GRENADE_LABEL = { smoke: '烟雾', flash: '闪光', he: '手雷', molotov: '燃烧瓶', incendiary: '燃烧弹', decoy: '诱饵', other: '其他' }
const personalGrenades = computed(() => {
  const name = selectedName.value
  const evs = ((grenadesMeta.value?.events) || []).filter((e) => name && e.player === name)
  const byType = {}
  for (const e of evs) { const t = e.type || 'other'; byType[t] = (byType[t] || 0) + 1 }
  return { total: evs.length, byType }
})

async function init() {
  await loadEntitlement()
  await loadPlayers()
}

onMounted(init)
</script>

<template>
  <div class="my">
    <!-- 玩家选择器：融入上下文（工具条，非独立大卡） -->
    <div class="picker">
      <label class="lbl" for="focus-picker">选择你在这场的位置</label>
      <select id="focus-picker" class="input sel" :value="focus?.player_id || ''" @change="selectPlayer(Number($event.target.value))">
        <option value="" disabled>选择本场你的玩家</option>
        <option v-for="p in players" :key="p.player_id" :value="p.player_id">
          {{ p.name }}（{{ p.team_identity }} 队 · {{ p.team }}）{{ p.is_you ? ' · 这是你' : '' }}
        </option>
      </select>
      <p v-if="!focus?.player_id" class="hint">选好后，个人分析、路线回放与击杀/死亡点都会围绕这位玩家展开。</p>
      <p v-if="viewingOther" class="hint other-hint">你正在查看其他玩家的视角：免费探索，仅浏览，不计入你的个人画像。</p>
    </div>

    <p v-if="error" class="form-error-box">{{ error }}</p>

    <!-- Free 锁定态（服务端 entitlement） -->
    <div v-if="!canPersonal" class="card locked-box">
      <div class="lock-icon">🔒</div>
      <p class="lock-title">个人复盘属于进阶版功能</p>
      <p class="lock-desc">升级后解锁：个人击杀/死亡点位、逐回合路线、交战转化率等个人分析。当前免费版仅展示基础摘要预览。</p>
    </div>

    <template v-else-if="focus?.player_id">
      <!-- 个人摘要：Rating Pro 唯一主结论（分数+置信/覆盖+一句原因）；K/D 等次级数据紧凑带状 -->
      <div v-if="summary" class="summary">
        <div class="sum-head">
          <span class="sum-name">{{ selectedName }}</span>
          <span class="tag" :class="summary.player.team_identity === 'A' ? 'tag-ct' : 'tag-t'">
            {{ summary.player.team_identity }} 队
          </span>
        </div>
        <RatingPro :rating="summary.rating_pro" />
        <div class="sum-strip train-strip">
          <div class="metric"><span class="metric-num">{{ summary.kills }}</span><span class="metric-lbl">击杀</span></div>
          <div class="metric"><span class="metric-num">{{ summary.deaths }}</span><span class="metric-lbl">死亡</span></div>
          <div class="metric metric-sec"><span class="metric-num">{{ summary.kd }}</span><span class="metric-lbl">K/D（次级）</span></div>
          <div class="metric"><span class="metric-num">{{ summary.adr }}</span><span class="metric-lbl">ADR</span></div>
          <div class="metric"><span class="metric-num">{{ summary.first_kills }}</span><span class="metric-lbl">首杀</span></div>
          <div class="metric"><span class="metric-num">{{ (summary.engagement_rate * 100).toFixed(1) }}%</span><span class="metric-lbl">交战转化率</span></div>
          <div class="metric"><span class="metric-num">{{ summary.rounds_alive }}/{{ summary.rounds_total }}</span><span class="metric-lbl">存活回合</span></div>
          <div class="metric"><span class="metric-num">{{ (summary.survival_rate * 100).toFixed(1) }}%</span><span class="metric-lbl">存活率</span></div>
        </div>
        <!-- 6R21F-F3：诚实标注——Rating Pro 低置信/低覆盖、或某分量零机会/缺字段时如实说明，绝不假装成 0 分或完整 -->
        <div v-if="summary.rating_pro" class="train-honest">
          <span v-if="summary.rating_pro.confidence === 'low'" class="wb-honest low"><span class="hs-dot" aria-hidden="true"></span>Rating Pro 样本有限（低置信），仅供参考</span>
          <span v-else-if="summary.rating_pro.confidence === 'medium'" class="wb-honest muted"><span class="hs-dot" aria-hidden="true"></span>Rating Pro 中等置信</span>
          <span v-if="summary.rating_pro.exclusions?.length" class="wb-honest muted"><span class="hs-dot" aria-hidden="true"></span>未计分分量：{{ summary.rating_pro.exclusions.map((e) => e.label).join('、') }}（无机会/数据缺失，非 0 分）</span>
        </div>
      </div>

      <!-- 6R8：个人战术回放（唯一主视觉；单回合 / 半场；仅所选玩家：路线 + 真实朝向/枪线 + 其投掷道具生命周期；我的道具/我的朝向开关） -->
      <div v-if="props.mapName" class="card pt-card">
        <div class="rr-head">
          <h3 class="sub-h">我的战术回放</h3>
          <span class="pill pill-muted">进阶版</span>
        </div>
        <PersonalTacticalReplay
          :demo-id="demoId"
          :map-name="props.mapName"
          :player-id="focus.player_id"
          :player-name="selectedName"
          :locate-request="locateRequest" />
      </div>

      <!-- 个人击杀/死亡图（点位/密度，主视觉；坐标只进诊断折叠） -->
      <div class="card combat">
        <div class="combat-title-row">
          <h3 class="sub-h">个人交战图</h3>
          <span class="pill pill-muted">进阶版</span>
        </div>
        <div class="combat-head">
          <div class="seg">
            <button class="seg-btn" :class="{ on: view === 'kills' }" @click="view = 'kills'">我的击杀</button>
            <button class="seg-btn" :class="{ on: view === 'deaths' }" @click="view = 'deaths'">我的死亡</button>
          </div>
          <div class="seg">
            <button v-for="s in ['all', 'CT', 'T']" :key="s" class="seg-btn sm" :class="{ on: sideFilter === s }"
                    @click="sideFilter = s">{{ s === 'all' ? '全部' : s }}</button>
          </div>
        </div>
        <PersonalCombatMap v-if="props.mapName" :demo-id="demoId" :map-name="props.mapName" :view-type="view" :side="sideFilter" />
      </div>

      <!-- 个人道具（只统计所选玩家的聚合计数；回合使用明细在个人战术回放的技术诊断里） -->
      <div v-if="canPersonal" class="card combat">
        <div class="combat-head"><h3 class="sub-h">我的道具</h3></div>
        <div v-if="!grenadesMeta" class="combat-empty">加载中…</div>
        <template v-else>
          <div class="gren-total">本场投掷 <b class="num">{{ personalGrenades.total }}</b> 个</div>
          <div class="gren-grid">
            <div v-for="t in ['smoke', 'flash', 'he', 'molotov', 'incendiary', 'decoy']" :key="t" class="gren-cell">
              <span class="gren-lbl">{{ GRENADE_LABEL[t] }}</span>
              <span class="num">{{ personalGrenades.byType[t] || 0 }}</span>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.my { display: flex; flex-direction: column; gap: 14px; }
.picker { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 10px 2px 14px; border-bottom: 1px solid var(--border); }
.lbl { font-size: 13px; color: var(--text-secondary); white-space: nowrap; }
.sel { max-width: 280px; flex: 1 1 220px; }
.hint { font-size: 12px; color: var(--text-muted); width: 100%; margin-top: 2px; }
.other-hint { color: var(--warn) !important; }

.summary { padding: 14px 2px; display: flex; flex-direction: column; gap: 12px; }
.sum-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sum-name {
  font-size: 19px; font-weight: 700;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; flex: 0 1 auto;
}
.sum-strip { display: flex; flex-wrap: wrap; gap: 0; border-top: 1px solid var(--border); padding-top: 12px; }
.sum-strip .metric { padding: 0 18px 0 0; margin-right: 18px; border-right: 1px solid var(--border); }
.sum-strip .metric:last-child { border-right: none; margin-right: 0; padding-right: 0; }
.formula { margin-top: 14px; font-size: 12.5px; color: var(--text-secondary); }
.train-honest { display: flex; flex-direction: column; gap: 5px; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--border); }

.combat-head { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.combat-title-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.combat-title-row .sub-h { margin: 0; }
.seg { display: flex; gap: 6px; }
.seg-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary);
  padding: 5px 14px; border-radius: 6px; font-size: 13px; cursor: pointer; }
.seg-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.seg-btn.sm { padding: 5px 10px; }
.legend { display: flex; gap: 16px; font-size: 12px; margin-bottom: 8px; }
.lg-k { color: var(--info); } .lg-d { color: var(--err); }
.combat-empty { color: var(--text-muted); text-align: center; padding: 24px; font-size: 13px; }
.combat-tbl { margin-top: 4px; }
.combat-map { margin-top: 12px; }
.combat-map .sub-h { margin: 0 0 10px; }
.combat-detail { margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elevated); padding: 8px 12px; }
.combat-detail summary { cursor: pointer; font-size: 12.5px; color: var(--text-secondary); }
.combat-detail .combat-tbl { margin-top: 10px; }
.note { color: var(--text-muted); font-size: 11.5px; margin-top: 8px; }
.gren-total { font-size: 14px; color: var(--text-secondary); }
.gren-total b { font-size: 20px; color: var(--text-primary); }
.gren-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-top: 14px; }
.gren-cell { display: flex; flex-direction: column; }
.gren-lbl { font-size: 12px; color: var(--text-muted); }
.gren-cell .num { font-size: 20px; font-weight: 700; }

.sub-h { font-size: 14px; font-weight: 700; color: var(--text-secondary); letter-spacing: .3px; }
.rr-card { padding: 20px; }
.rr-head { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.rr-head .sub-h { margin: 0; }

@media (max-width: 720px) {
  .sum-strip .metric { padding-right: 10px; margin-right: 10px; }
  .sum-strip .metric:nth-last-child(2) { border-right: none; }
}
</style>
