<script setup>
/**
 * 6R15：长期教练（Long-form Coach）页面。
 * - 主体 = 当前登录用户的 active SteamIdentity（服务端权威；这里只做展示与前端 gating）。
 * - Free：只读锁定预览（进阶版）；Pro：最近 10 场；Max：最近 30 场 + 10/30 对照。
 * - 信息密度像教练笔记：一句画像摘要、5/10/30 场覆盖、10/30 切换、重复问题、趋势、Top 3。
 * - 证据跳转：点击「查看证据」→ /demo/{demo_id}?view=personal&locate=<json>，Report 页自动定位回合+时间+图层+高亮。
 * - 诚实边界：loading/empty/error/locked/stale/underfilled 全部中文状态；不渲染裸 tick / 世界坐标 / 完整 SteamID。
 * - 移动端 360/390 无横滚、触控 >=44px、长名受控。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { authStore } from '../stores/auth'
import { api, ApiError } from '../api/client'
import { isPro, isMax, tierInfo } from '../utils/subscription'

const router = useRouter()
const user = computed(() => authStore.state.user)

const profile = ref(null)
const loading = ref(true)
const error = ref('')
const lockedNotice = ref('')
const windowSel = ref(10)   // 10 | 30

const canPro = computed(() => isPro(user.value))
const canMax = computed(() => isMax(user.value))
const tier = computed(() => tierInfo(user.value))

const status = computed(() => profile.value?.states?.status || 'ok')
// 前端 gating：非 Pro（或订阅过期）→ 锁定；服务端可返回 locked（如未绑定 Steam）
const locked = computed(() => !canPro.value || status.value === 'locked')

const summaryText = computed(() => profile.value?.summary || profile.value?.rendered?.deterministic || '')

// ---------- 覆盖 5/10/30（实际 K/10、K/30；少场次显示真实数，绝不补假比赛） ----------
function winCoverage(key, defMax) {
  const o = profile.value?.coverage?.[key]
  if (o && typeof o.available === 'number') return { available: o.available, max: o.max || defMax }
  const numKey = key.replace('window_', '')
  const arr = Array.isArray(profile.value?.windows?.[numKey]) ? profile.value.windows[numKey] : null
  if (arr) return { available: arr.length, max: Number(numKey) || defMax }
  return null
}
const coverage = computed(() => ({
  f5: winCoverage('window_5', 5),
  f10: winCoverage('window_10', 10),
  f30: winCoverage('window_30', 30),
}))
const c10 = computed(() => coverage.value.f10)
const c30 = computed(() => coverage.value.f30)

// ---------- 主要数据 ----------
const repeatedProblems = computed(() => (profile.value?.repeated_problems || []).filter(Boolean))
const trends = computed(() => (profile.value?.trends || []).filter(Boolean))
const top3 = computed(() => (profile.value?.top3 || []).filter(Boolean))

const hasEmpty = computed(() => status.value === 'empty' || (!repeatedProblems.value.length && !trends.value.length && !top3.value.length && !summaryText.value))

// ---------- 格式化（不裸显示 raw 值） ----------
function fmtPct(v) { return v == null ? '—' : `${Math.round(Number(v) * 100)}%` }
function fmtRate(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  // 0..1 视为率 → 百分比；否则视为有量纲数值（如把 ADR 当作数）
  return (n >= 0 && n <= 1) ? `${Math.round(n * 100)}%` : n.toFixed(1)
}
const CONF_CN = { high: '高', medium: '中', low: '低' }
const CONF_CLS = { high: 'pill-ok', medium: 'pill-info', low: 'pill-muted' }
const TREND_CN = { improving: '改善', regressing: '退步', flat: '持平', insufficient: '尚无法判断' }
const TREND_CLS = { improving: 'ok', regressing: 'err', flat: 'muted', insufficient: 'muted' }
const MAG_CN = { small: '小幅', medium: '中等', large: '明显' }

// 6R21 教练图形：用真实 rate 渲染可视化（前后对比条 / 频率条 / Top3 优先级），不伪造数据。
function _clamp01(v) { return v == null ? null : Math.max(0, Math.min(1, Number(v))) }
const trendVisuals = computed(() => trends.value.map((t) => {
  const prev = _clamp01(t.previous); const cur = _clamp01(t.current)
  const scale = Math.max(1e-6, prev ?? 0, cur ?? 0)
  return {
    id: t.candidate_id, trend: t.trend,
    prev: prev == null ? null : Number((prev * 100).toFixed(1)),
    cur: cur == null ? null : Number((cur * 100).toFixed(1)),
    prevW: prev == null ? 0 : (prev / scale) * 100,
    curW: cur == null ? 0 : (cur / scale) * 100,
    cls: TREND_CLS[t.trend] || 'muted',
    label: TREND_CN[t.trend] || t.trend || '—',
  }
}))
const problemVisuals = computed(() => repeatedProblems.value.map((p) => ({
  id: p.candidate_id, rate: _clamp01(p.rate),
  width: _clamp01(p.rate) == null ? 0 : Number((_clamp01(p.rate) * 100).toFixed(1)),
  conf: p.confidence || 'low',
})))
const top3Visuals = computed(() => top3.value.map((t) => ({
  id: t.candidate_id, rank: t.rank,
  conf: t.confidence || 'low',
  repeat: _clamp01(t.repeat),
})))

// ---------- 证据 ----------
function problemWhy(p) { return p?.detail?.why || p?.detail?.impact_human || '' }
function problemAdvice(p) { return p?.detail?.advice || '' }
// 30 场窗口为 Max-only；Pro 可见但锁定
const v30locked = computed(() => !canMax.value)
function evidenceOf(item) {
  const arr = Array.isArray(item?.evidence) ? item.evidence
    : Array.isArray(item?.detail?.evidence) ? item.detail.evidence : []
  return arr.filter((e) => e && e.demo_id != null)
}
function evidenceCount(item) {
  const n = Number(item?.evidence_count ?? item?.detail?.evidence_count ?? evidenceOf(item).length)
  return Number.isFinite(n) ? n : 0
}
function evidenceLabel(ev) {
  const parts = []
  if (ev.round_no != null) parts.push(`回合 ${ev.round_no}`)
  if (ev.region && ev.region !== '未知区域') parts.push(ev.region)
  if (ev.seconds != null) parts.push(`${Number(ev.seconds).toFixed(1)} 秒`)
  else parts.push('回合开始')
  return parts.join(' · ')
}
function jumpToEvidence(ev) {
  const payload = {
    round: Number(ev.round_no),
    time: ev.seconds == null ? null : Number(ev.seconds),
    layers: Array.isArray(ev.layers) ? ev.layers : [],
    highlight: Array.isArray(ev.highlight) ? ev.highlight : Array.isArray(ev.players) ? ev.players : [],
  }
  router.push({
    path: `/demo/${ev.demo_id}`,
    query: { view: 'personal', locate: JSON.stringify(payload) },
  })
}

// ---------- 加载与切换 ----------
async function load(windowValue = windowSel.value) {
  loading.value = true
  error.value = ''
  lockedNotice.value = ''
  try {
    profile.value = await api.getCoachProfile(windowValue)
  } catch (e) {
    profile.value = null
    error.value = e instanceof ApiError ? e.message : '画像暂时不可用，请稍后重试'
  } finally {
    loading.value = false
  }
}

function selectWindow(v) {
  if (v === 30 && !canMax.value) { lockedNotice.value = '最近 30 场对照需要高阶版（Max）权限'; return }
  if (v === windowSel.value) return
  windowSel.value = v
  load(v)
}

function goUpgrade() {
  // 升级通道尚未开放支付，先引导到个人中心（与既有「支付功能即将开放」一致）
  router.push({ name: 'profile' })
}

// 6R17.2：编辑部式——Top3 优先 + 紧凑问题台账（默认前 3 行、其余折叠；行内展开详情，保留全部证据跳转逻辑）
const VISIBLE_PROBLEMS = 3
const showAllProblems = ref(false)
const expandedProblems = ref(new Set())
const visibleProblems = computed(() => showAllProblems.value ? repeatedProblems.value : repeatedProblems.value.slice(0, VISIBLE_PROBLEMS))
function toggleProblem(id) {
  const s = new Set(expandedProblems.value)
  if (s.has(id)) s.delete(id); else s.add(id)
  expandedProblems.value = s
}

onMounted(() => {
  if (canPro.value) load(10)
  else loading.value = false   // Free：直接呈现锁定预览
})
</script>

<template>
  <div class="page-wrap coach">
    <div class="coach-head page-head">
      <div class="coach-lead">
        <p class="eyebrow">赛后复盘室 / 教练笔记</p>
        <h1 class="page-title">长期教练</h1>
        <p class="page-sub">以你绑定的 Steam 身份持续累计最近 10 / 30 场；能证明才显示，不能证明就诚实降级为中文状态。</p>
      </div>
      <div class="coach-badges">
        <span v-if="canPro" class="pill" :class="tier.cls">{{ tier.label }}</span>
        <span v-else class="pill pill-muted">免费预览</span>
      </div>
    </div>

    <!-- 锁定预览（Free / 未授权） -->
    <div v-if="locked" class="card locked-box">
      <div class="lock-icon">🔒</div>
      <p class="lock-title">{{ status === 'locked' ? '画像暂不可用' : '长期教练属于进阶版功能' }}</p>
      <p class="lock-desc">
        {{
          status === 'locked'
            ? '尚未完成身份绑定或数据不足，无法生成你的长期画像。'
            : '升级后解锁：基于你最近 10 场比赛的长期画像——重复问题、趋势变化、最值得改的 3 件事，并能点击证据回到对应回合。'
        }}
      </p>
      <div class="lock-cta">
        <button class="btn btn-primary" @click="goUpgrade">了解升级（月卡 39 元 / 年卡 299 元）</button>
        <span class="lock-hint">支付功能即将开放</span>
      </div>
    </div>

    <!-- 已授权 -->
    <template v-else>
      <div v-if="loading" class="loading-row">正在生成你的长期画像…</div>
      <p v-else-if="error" class="error-state">{{ error }}</p>

      <template v-else-if="profile">
        <!-- 诚实状态条 -->
        <p v-if="status === 'stale'" class="state-banner warn">快照基于旧版本数据，已降级为确定性表达。</p>
        <p v-if="status === 'underfilled'" class="state-banner muted">
          当前样本较少（{{ (c10?.available ?? c30?.available ?? 0) }}/{{ c10?.max ?? c30?.max ?? 10 }}），判断仅供参考。
        </p>

        <!-- 摘要 + 覆盖 + 10/30 切换 -->
        <div class="sum-card coach-command">
          <div class="coach-command-lead">
            <span class="command-kicker">当前训练重点</span>
            <p class="summary-text" v-if="summaryText">{{ summaryText }}</p>
            <p class="coach-disclosure">结论仅来自已完成复盘；覆盖、置信与证据不足时不会补齐或外推。</p>
          </div>

          <div class="cov metric-grid coach-command-metrics">
            <div v-if="coverage.f5" class="cov-cell metric-cell">
              <span class="mc-num cov-num num">{{ coverage.f5.available }}/{{ coverage.f5.max }}</span>
              <span class="mc-lbl cov-lbl">最近 5 场</span>
            </div>
            <div class="cov-cell metric-cell" :class="{ on: windowSel === 10 }">
              <span class="mc-num cov-num num">{{ c10 ? `${c10.available}/${c10.max}` : '—' }}</span>
              <span class="mc-lbl cov-lbl">最近 10 场</span>
            </div>
            <div class="cov-cell metric-cell" :class="{ on: windowSel === 30 }">
              <span class="mc-num cov-num num">{{ c30 ? `${c30.available}/${c30.max}` : '—' }}</span>
              <span class="mc-lbl cov-lbl">最近 30 场</span>
            </div>
          </div>

          <div class="coach-command-foot">
            <span class="window-label">观察窗口</span>
            <div class="toggle" role="group" aria-label="统计窗口">
              <button class="seg-btn" :class="{ on: windowSel === 10 }" @click="selectWindow(10)">最近 10 场</button>
              <button class="seg-btn" :class="{ on: windowSel === 30, locked: v30locked }" :disabled="!canMax" @click="selectWindow(30)">
                最近 30 场<span v-if="!canMax" class="lock-tag">高阶版</span>
              </button>
            </div>
          </div>
          <p v-if="lockedNotice" class="toggle-note">{{ lockedNotice }}</p>
        </div>

        <!-- 空态 -->
        <div v-if="hasEmpty" class="empty-state">还没有足够的已完成复盘来生成画像</div>

        <template v-else>
          <div class="coach-workspace">
            <div class="coach-primary">
          <!-- Top 3 优先事项（编辑部式，置于最前） -->
          <section class="coach-sec coach-top3">
            <div class="section-head">
              <h2 class="sub-h">最值得改的 {{ top3.length }} 件事</h2>
              <span class="sh-note">按影响 × 重复性 × 置信度 × 近期性排序</span>
            </div>
            <p v-if="!top3.length" class="muted">暂未识别到需要优先改进的重复问题。</p>
            <div v-else class="top3-list">
              <div v-for="t in top3" :key="t.candidate_id" class="top3-item">
                <div class="top3-head">
                  <span class="rank num">{{ t.rank }}.</span>
                  <span class="item-title">{{ t.human_name || t.candidate_id }}</span>
                  <span class="top3-meta num">{{ t.confidence ? ('置信 ' + (CONF_CN[t.confidence] || t.confidence)) : '' }}{{ t.repeat != null ? ' · ' + fmtPct(t.repeat) : '' }}{{ evidenceCount(t) ? ' · ' + evidenceCount(t) + ' 条证据' : '' }}</span>
                </div>
                <div class="top3-visual" aria-hidden="true">
                  <span class="tv-label">重复度</span>
                  <span class="tv-track"><span class="tv-fill" :style="{ width: (top3Visuals.find((v) => v.id === t.candidate_id)?.repeat ?? 0) * 100 + '%' }"></span></span>
                  <span class="tv-conf" :class="`pill ${CONF_CLS[t.confidence] || 'pill-muted'}`">{{ CONF_CN[t.confidence] || t.confidence || '—' }} 置信</span>
                </div>
                <p v-if="t.why" class="item-line">{{ t.why }}</p>
                <p v-if="t.advice" class="item-line item-advice"><b>下一步：</b>{{ t.advice }}</p>
                <div v-if="evidenceOf(t).length" class="ev-list">
                  <button v-for="(ev, i) in evidenceOf(t)" :key="i" class="ev-btn" :data-demo-id="ev.demo_id" :data-round="ev.round_no" :data-time="ev.seconds == null ? '' : ev.seconds" @click="jumpToEvidence(ev)">
                    <span class="ev-lbl">{{ evidenceLabel(ev) }}</span><span class="ev-go">查看证据 →</span>
                  </button>
                </div>
                <span v-else class="muted item-el">暂无精确定位证据</span>
              </div>
            </div>
          </section>
            </div>

            <aside class="coach-analysis-rail" aria-label="重复问题与趋势">

          <!-- 重复问题（紧凑问题台账；默认前 3 行，其余折叠） -->
          <section class="coach-sec">
            <div class="section-head">
              <h2 class="sub-h">重复问题</h2>
              <span class="sh-note">跨场反复出现的行为；单场出现不计入「重复」</span>
            </div>
            <p v-if="!repeatedProblems.length" class="muted">暂未发现跨场重复问题；单场出现的问题不计为「重复」。</p>
            <div v-else class="issue-ledger">
              <div v-for="p in visibleProblems" :key="p.candidate_id" class="issue-row">
                <div class="issue-main">
                  <span class="issue-name">{{ p.human_name || p.candidate_id }}</span>
                  <span class="issue-rate num">{{ p.hits }}/{{ p.matches }} 场（{{ fmtPct(p.rate) }}）</span>
                  <span class="rate-bar" :style="{ width: (problemVisuals.find((v) => v.id === p.candidate_id)?.width ?? 0) + '%' }" aria-hidden="true"></span>
                </div>
                <span class="issue-meta">{{ p.confidence ? ('置信 ' + (CONF_CN[p.confidence] || p.confidence)) : '' }}</span>
                <button class="issue-expand" @click="toggleProblem(p.candidate_id)" :aria-expanded="expandedProblems.has(p.candidate_id)">
                  {{ expandedProblems.has(p.candidate_id) ? '收起' : '详情' }}
                </button>
                <div v-if="expandedProblems.has(p.candidate_id)" class="issue-detail">
                  <p v-if="problemWhy(p)" class="item-line"><b>为什么：</b>{{ problemWhy(p) }}</p>
                  <p v-if="problemAdvice(p)" class="item-line"><b>下一步：</b>{{ problemAdvice(p) }}</p>
                  <div v-if="evidenceOf(p).length" class="ev-list">
                    <button v-for="(ev, i) in evidenceOf(p)" :key="i" class="ev-btn" :data-demo-id="ev.demo_id" :data-round="ev.round_no" :data-time="ev.seconds == null ? '' : ev.seconds" @click="jumpToEvidence(ev)">
                      <span class="ev-lbl">{{ evidenceLabel(ev) }}</span><span class="ev-go">查看证据 →</span>
                    </button>
                  </div>
                  <span v-else class="muted item-el">暂无精确定位证据</span>
                </div>
              </div>
              <button v-if="repeatedProblems.length > VISIBLE_PROBLEMS" class="coach-more" @click="showAllProblems = !showAllProblems">
                {{ showAllProblems ? '收起问题台账' : `展开其余 ${repeatedProblems.length - VISIBLE_PROBLEMS} 个` }}
              </button>
            </div>
          </section>

          <!-- 趋势（紧凑行式） -->
          <section class="coach-sec">
            <div class="section-head">
              <h2 class="sub-h">趋势</h2>
              <span class="sh-note">前后窗口对比；样本不足时诚实标注「尚无法判断」</span>
            </div>
            <p v-if="!trends.length" class="muted">暂无足够数据来判断趋势。</p>
            <div v-else class="trend-list">
              <div v-for="t in trends" :key="t.candidate_id" class="trend-row">
                <span class="issue-name">{{ t.human_name || t.candidate_id }}</span>
                <span class="trend-state" :class="TREND_CLS[t.trend]">{{ TREND_CN[t.trend] || t.trend || '—' }}</span>
                <div class="trend-bars" aria-hidden="true">
                  <span class="tb-pair"><i class="tb-label">前</i><span class="tb-track"><i class="tb-fill" :class="TREND_CLS[t.trend]" :style="{ width: (trendVisuals.find((v) => v.id === t.candidate_id)?.prevW ?? 0) + '%' }"></i></span><b class="tb-val num">{{ trendVisuals.find((v) => v.id === t.candidate_id)?.prev ?? '—' }}</b></span>
                  <span class="tb-pair"><i class="tb-label">后</i><span class="tb-track"><i class="tb-fill" :class="TREND_CLS[t.trend]" :style="{ width: (trendVisuals.find((v) => v.id === t.candidate_id)?.curW ?? 0) + '%' }"></i></span><b class="tb-val num">{{ trendVisuals.find((v) => v.id === t.candidate_id)?.cur ?? '—' }}</b></span>
                </div>
                <span class="trend-detail">{{ t.status === 'insufficient' ? '尚无法判断（样本不足）' : `前 ${fmtRate(t.previous)} → 后 ${fmtRate(t.current)}（${MAG_CN[t.magnitude] || ''} ${(t.trend === 'improving' ? '改善' : t.trend === 'regressing' ? '退步' : '持平')}）` }}</span>
              </div>
            </div>
          </section>
            </aside>
          </div>

          <!-- 确定性模板正文（折叠，便于复核） -->
          <details class="det">
            <summary>确定性模板正文</summary>
            <p class="det-body">{{ profile?.rendered?.deterministic || summaryText || '—' }}</p>
          </details>
        </template>
      </template>
    </template>
  </div>
</template>

<style scoped>
.coach { max-width: 1180px; }
.coach-lead { min-width: 0; }
.coach-head .page-sub { margin-top: 8px; max-width: 62ch; }
.coach-badges { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

/* 锁定预览 */
.locked-box { display: flex; flex-direction: column; gap: 8px; padding: 22px 22px; }
.locked-box .lock-icon { font-size: 20px; }
.locked-box .lock-title { font-weight: 700; font-size: 15px; }
.locked-box .lock-desc { color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.lock-cta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 6px; }
.lock-cta .btn { min-height: 44px; }
.lock-hint { color: var(--text-muted); font-size: 12px; }

/* 诚实状态条 */
.state-banner { padding: 9px 12px; border-radius: var(--radius-sm); font-size: 13px; line-height: 1.6; margin: 0 0 14px; }
.state-banner.warn { color: var(--warn); background: var(--warn-soft); border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent); }
.state-banner.muted { color: var(--text-secondary); background: var(--bg-elevated); border: 1px solid var(--border); }

/* 首屏指挥条：先给训练结论，再给覆盖与窗口，不把用户扔进数据墙。 */
.sum-card { display: flex; flex-direction: column; gap: 14px; padding: 18px 18px; background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius-lg); margin-bottom: 8px; }
.coach-command {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(330px, .9fr);
  gap: 18px 28px;
  margin-top: 18px;
  padding: 22px 24px;
  border-left: 3px solid var(--accent);
  background: linear-gradient(108deg, rgba(230,165,74,.07), transparent 38%), var(--bg-panel);
}
.coach-command-lead { min-width: 0; align-self: center; }
.command-kicker, .window-label { color: var(--accent); font-size: 11px; font-weight: 800; letter-spacing: .12em; }
.coach-command .summary-text { color: var(--text-primary); font-size: 16px; line-height: 1.75; margin-top: 8px; max-width: 60ch; }
.coach-disclosure { color: var(--text-muted); font-size: 11.5px; line-height: 1.65; margin-top: 8px; }
.coach-command-metrics { grid-template-columns: repeat(3, minmax(90px, 1fr)); align-self: stretch; }
.coach-command-foot { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 14px; padding-top: 14px; border-top: 1px solid var(--border); }
.cov { row-gap: 10px; }
.cov-cell { display: flex; flex-direction: column; gap: 3px; min-width: 0; padding: 10px 14px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--bg-elevated); }
.cov-cell.on { border-color: var(--accent); background: var(--accent-soft); }
.cov-num { font-size: 24px; font-weight: 800; line-height: 1.05; color: var(--text-primary); }
.cov-lbl { font-size: var(--f-label); color: var(--text-muted); }
.summary-text { color: var(--text-secondary); font-size: 14px; line-height: 1.8; margin: 0; }
.toggle { display: flex; gap: 8px; flex-wrap: wrap; }
.toggle .seg-btn { min-height: 44px; flex: 0 1 auto; }
.toggle .seg-btn.locked, .toggle .seg-btn:disabled { opacity: 0.7; }
.lock-tag { font-size: 10px; background: var(--accent); color: #14100a; padding: 1px 6px; border-radius: 4px; margin-left: 6px; vertical-align: 1px; white-space: nowrap; }
.toggle-note { color: var(--warn); font-size: 12.5px; margin: 0; }

/* 列表区块 */
.coach-sec { margin-top: 24px; }
.coach-sec .section-head { margin-top: 0; }
.coach-workspace { display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(360px, .92fr); gap: 34px; align-items: start; }
.coach-primary, .coach-analysis-rail { min-width: 0; }
.coach-primary .coach-sec, .coach-analysis-rail .coach-sec { margin-top: 24px; }
.coach-analysis-rail { display: contents; }
.coach-analysis-rail > .coach-sec { min-width: 0; }
.coach-analysis-rail > .coach-sec:first-child { grid-column: 2; border-left: 1px solid var(--border); padding-left: 28px; }
.coach-analysis-rail > .coach-sec:last-child { grid-column: 1 / -1; border-top: 1px solid var(--border); padding-top: 24px; }
.coach-analysis-rail .section-head { align-items: flex-start; flex-direction: column; gap: 3px; }

.item { padding: 14px 16px; margin-top: 10px; }
.item-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.item-title { font-size: 15px; font-weight: 700; color: var(--text-primary); min-width: 0; }
.item-line { color: var(--text-secondary); font-size: 13px; line-height: 1.7; margin-top: 8px; }
.item-line b { color: var(--text-primary); font-weight: 600; }
.item-meta { color: var(--text-muted); font-size: 12px; margin-top: 8px; }
.item-el { font-size: 12px; margin-top: 8px; }
.rank { font-size: 17px; font-weight: 800; color: var(--accent); margin-right: 2px; }
.pill.warn2 { color: var(--warn); background: var(--warn-soft); }

/* 证据按钮 */
.ev-list { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 10px; }
.ev-btn { display: flex; align-items: center; gap: 10px; justify-content: space-between; width: 100%; text-align: left;
  border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-primary);
  padding: 8px 12px; border-radius: var(--radius-sm); font-size: 12.5px; cursor: pointer; min-height: 38px; }
.ev-btn:hover { border-color: var(--accent); }
.ev-lbl { color: var(--text-secondary); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; }
.ev-go { color: var(--accent); font-weight: 600; flex-shrink: 0; white-space: nowrap; }

/* 确定性模板折叠 */
.det { margin-top: 22px; padding: 12px 16px; }
.det summary { cursor: pointer; font-size: 13px; color: var(--accent); }
.det-body { margin-top: 10px; color: var(--text-secondary); font-size: 13px; line-height: 1.8; white-space: pre-wrap; }

/* 6R17.2：编辑部式 Top3 + 紧凑问题台账 + 趋势行（去大卡/胶囊墙） */
.coach-top3 { margin-top: 20px; }
.top3-list { display: flex; flex-direction: column; gap: 0; border-top: 1px solid var(--border); }
.top3-item { padding: 17px 0; border-bottom: 1px solid var(--border); }
.top3-item:last-child { border-bottom: none; }
.top3-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.top3-head .rank { flex-shrink: 0; }
.top3-meta { color: var(--text-muted); font-size: 12px; white-space: nowrap; }
.item-advice b { color: var(--accent); }
.issue-ledger { border-top: 1px solid var(--border); }
.issue-row { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.issue-main { display: flex; align-items: baseline; gap: 12px; min-width: 0; flex: 1 1 auto; }
.issue-name { font-weight: 700; font-size: 14px; color: var(--text-primary); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.issue-rate { color: var(--text-secondary); font-size: 12.5px; white-space: nowrap; }
.issue-meta { color: var(--text-muted); font-size: 12px; white-space: nowrap; flex-shrink: 0; }
.issue-expand { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 6px 12px; border-radius: var(--radius-sm); font-size: 12.5px; cursor: pointer; font-weight: 600; flex-shrink: 0; min-height: 38px; }
.issue-expand:hover { border-color: var(--accent); color: var(--accent); }
.issue-detail { flex-basis: 100%; margin-top: 6px; padding-left: 2px; }
.issue-detail .item-line { margin-top: 4px; }
.coach-more { margin: 12px auto 0; display: block; border: none; background: transparent; color: var(--accent); font-size: 13px; font-weight: 600; cursor: pointer; min-height: 44px; padding: 6px 12px; }
.trend-list { border-top: 1px solid var(--border); }
.trend-row { display: grid; grid-template-columns: minmax(140px, .72fr) auto minmax(220px, 1fr) minmax(200px, 1fr); gap: 5px 16px; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--border); }
.trend-row:last-child { border-bottom: none; }
.trend-state { font-weight: 700; font-size: 12.5px; flex-shrink: 0; }
.trend-state.ok { color: var(--ok); } .trend-state.err { color: var(--err); }
.trend-state.flat { color: var(--text-muted); } .trend-state.muted { color: var(--text-muted); }
.trend-detail { color: var(--text-secondary); font-size: 13px; min-width: 0; }

/* 6R21 教练图形（真实 rate 可视化，不伪造） */
.top3-visual { display: flex; align-items: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
.tv-label, .tb-label { color: var(--text-muted); font-size: 11px; }
.tv-track { position: relative; flex: 1 1 160px; min-width: 120px; height: 7px; border-radius: 4px; background: rgba(152,164,179,.16); overflow: hidden; }
.tv-fill { display: block; height: 100%; border-radius: 4px; background: linear-gradient(90deg, var(--accent), var(--accent-strong)); }
.tv-conf { font-size: 11px; }
.rate-bar { display: block; height: 7px; border-radius: 4px; background: var(--info); }
.issue-main { position: relative; }
.issue-main .rate-bar { margin-top: 6px; max-width: 260px; }
.trend-bars { display: flex; flex-direction: column; gap: 5px; flex: 1 1 180px; min-width: 150px; }
.tb-pair { display: flex; align-items: center; gap: 8px; }
.tb-label { flex-shrink: 0; width: 20px; }
.tb-track { position: relative; flex: 1 1 auto; height: 7px; border-radius: 4px; background: rgba(152,164,179,.16); overflow: hidden; }
.tb-fill { display: block; height: 100%; border-radius: 4px; }
.tb-fill.ok { background: var(--ok); }
.tb-fill.err { background: var(--err); }
.tb-fill.flat, .tb-fill.muted { background: var(--text-faint); }
.tb-val { color: var(--text-secondary); font-size: 11.5px; width: 42px; text-align: right; }

@media (max-width: 720px) {
  .coach-head { flex-wrap: wrap; }
  .coach-badges { margin-left: 0; }
  .cov { row-gap: 8px; }
  .item-head { gap: 6px; }
  .item-title { flex: 1 1 100%; }
  .ev-btn { min-height: 44px; }
  .toggle .seg-btn { flex: 1 1 100%; }
  .coach-command { grid-template-columns: 1fr; padding: 18px 16px; gap: 16px; }
  .coach-command-foot { align-items: flex-start; flex-direction: column; }
  .coach-command-foot .toggle { width: 100%; }
  .coach-command-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .cov-cell { padding: 10px 8px; }
  .cov-num { font-size: 20px; }
  .coach-workspace { grid-template-columns: 1fr; gap: 0; }
  .coach-analysis-rail { display: block; }
  .coach-analysis-rail > .coach-sec:first-child, .coach-analysis-rail > .coach-sec:last-child { grid-column: auto; border-left: 0; padding-left: 0; }
  .coach-analysis-rail > .coach-sec:last-child { border-top: 0; padding-top: 0; }
  .trend-row { grid-template-columns: minmax(0, 1fr) auto; }
  .trend-row .trend-bars, .trend-row .trend-detail { grid-column: 1 / -1; }
}

@media (min-width: 721px) and (max-width: 980px) {
  .coach-command { grid-template-columns: 1fr; }
  .coach-workspace { grid-template-columns: 1fr; }
  .coach-analysis-rail { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  .coach-analysis-rail > .coach-sec:first-child, .coach-analysis-rail > .coach-sec:last-child { grid-column: auto; border-left: 0; border-top: 0; padding: 0; }
  .trend-row { grid-template-columns: minmax(0, 1fr) auto; }
  .trend-row .trend-bars, .trend-row .trend-detail { grid-column: 1 / -1; }
}
</style>
