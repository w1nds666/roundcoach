<script setup>
/**
 * 6R10B：Rating Pro v1 主指标卡
 * - 总分 + 版本 + provisional 标记 + 人话解释 + 置信度/覆盖 + 参考范围
 * - 可展开「技术口径」：各分量权重/公式/校验常数/不可用原因
 * - 明确标注「不等于 HVTV 官方 Rating」
 * - rating=null 时显示不可用原因（不当作 0 分）
 */
import { computed, ref } from 'vue'

const props = defineProps({
  rating: { type: Object, default: null },
  compact: { type: Boolean, default: false },
})

const openTech = ref(false)

const unavailable = computed(() => !props.rating || props.rating.rating == null || props.rating.status !== 'available')

const scoreColor = computed(() => {
  if (unavailable.value) return 'muted'
  const r = props.rating.rating
  if (r >= 1.25) return 'ok'
  if (r >= 1.0) return 'accent'
  if (r >= 0.8) return 'warn'
  return 'err'
})

const confLabel = { high: '数据充分', medium: '中等置信', low: '样本有限' }
</script>

<template>
  <div class="rating-card" :class="{ compact }">
    <template v-if="unavailable">
      <div class="rc-unavail">
        <div class="rc-lock">⚠</div>
        <p class="rc-reason">{{ rating?.reason || '该玩家暂无 Rating Pro 数据。' }}</p>
      </div>
    </template>

    <template v-else>
      <div class="rc-head">
        <div class="rc-score" :class="scoreColor">
          <span class="rc-num">{{ rating.rating.toFixed(2) }}</span>
          <span class="rc-cap">Rating Pro<em v-if="rating.provisional">（试验性）</em></span>
        </div>
        <div class="rc-meta">
          <span class="pill pill-ok">{{ confLabel[rating.confidence] || rating.confidence }}</span>
          <span class="pill pill-muted">覆盖 {{ rating.coverage.rounds }} 回合</span>
        </div>
      </div>

      <!-- 默认：只留「一句人话原因」（两行受控）；公式/分量/完整免责折叠进「评分口径」 -->
      <p v-if="!compact" class="rc-explain">{{ rating.explanation }}</p>

      <details v-if="!compact" class="rc-tech" @toggle="openTech = $event.target.open">
        <summary>评分口径（为什么是这个分）</summary>
        <div class="rc-tech-body">
          <div class="rc-tech-row"><span>版本</span><b>{{ rating.version }}</b></div>
          <div class="rc-tech-row"><span>口径</span><b class="rc-scope">{{ rating.reference_scope }}</b></div>
          <table class="rc-table">
            <thead>
              <tr><th>分量</th><th>权重</th><th>值</th><th>公式</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in rating.components" :key="c.key">
                <td>{{ c.label }}<em v-if="c.status !== 'ok'" class="rc-tag">{{ c.status }}</em></td>
                <td>{{ (c.weight * 100).toFixed(0) }}%</td>
                <td>{{ c.value == null ? '—' : (c.key === 'adr' ? c.value.toFixed(0) : c.value.toFixed(2)) }}</td>
                <td class="rc-formula">{{ c.formula }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="rating.exclusions?.length" class="rc-excl">
            未计分分量：<span v-for="(e, i) in rating.exclusions" :key="e.component">
              {{ e.label }}<span v-if="i < rating.exclusions.length - 1">、</span>
            </span><span class="muted">（无机会/数据缺失，非 0 分）</span>
          </p>
          <p class="rc-not-hltv">{{ rating.not_hltv_note }}</p>
        </div>
      </details>
    </template>
  </div>
</template>

<style scoped>
.rating-card { display: grid; gap: 10px; padding: 6px 0; }
.rc-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.rc-score { display: flex; align-items: baseline; gap: 10px; }
.rc-num { font-size: 46px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.rc-cap { font-size: 13px; color: var(--text-secondary); }
.rc-cap em { font-style: normal; color: var(--text-muted); font-size: 11px; }
.rc-score.ok .rc-num { color: var(--ok); }
.rc-score.accent .rc-num { color: var(--accent); }
.rc-score.warn .rc-num { color: var(--warn); }
.rc-score.err .rc-num { color: var(--err); }
.rc-score.muted .rc-num { color: var(--text-muted); }
.rc-meta { display: flex; gap: 8px; }
.rc-explain {
  color: var(--text-secondary); font-size: 13px; line-height: 1.6;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; text-overflow: ellipsis; max-width: 72ch;
}
.rc-tech { margin-top: 2px; border-top: 1px solid var(--border); padding-top: 8px; }
.rc-tech summary { cursor: pointer; font-size: 13px; color: var(--accent); list-style: none; }
.rc-tech summary::before { content: '▸ '; }
.rc-tech[open] summary::before { content: '▾ '; }
.rc-tech-body { margin-top: 12px; display: grid; gap: 12px; }
.rc-tech-row { display: flex; gap: 10px; font-size: 12px; color: var(--text-secondary); align-items: baseline; }
.rc-tech-row span { min-width: 52px; }
.rc-tech-row b { color: var(--text-primary); font-weight: 600; }
.rc-scope { font-weight: 400 !important; }
.rc-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.rc-table th, .rc-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
.rc-table th { color: var(--text-muted); font-weight: 600; }
.rc-table em { font-style: normal; color: var(--warn); font-size: 11px; margin-left: 4px; }
.rc-formula { color: var(--text-muted); }
.rc-excl { font-size: 12px; color: var(--text-secondary); }
.rc-not-hltv { font-size: 12px; color: var(--text-muted); }
.rc-unavail { padding: 10px 0; }
.rc-lock { font-size: 22px; color: var(--text-muted); }
.rc-reason { color: var(--text-secondary); font-size: 13px; }
@media (max-width: 720px) { .rc-num { font-size: 38px; } }
</style>
