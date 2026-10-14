<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { MorphIcon } from 'morphicons/vue'
import { ArrowRight } from 'lucide'
import LandingControlPreview from '../components/LandingControlPreview.vue'
import LandingStage from '../components/LandingStage.vue'
import { authStore } from '../stores/auth'

const sample = ref(null)
const loadError = ref(false)
const landingRoot = ref(null)
const authed = computed(() => !!authStore.state.user)
let revealObserver = null
let stageMotionFrame = 0
let coachPreviewTimer = 0
let coachRevealTimer = 0
let coachPreviewStarted = false
let introPrepareFrame = 0
let introPlayFrame = 0
const coachPreviewCount = ref(0)
const coachExamplesRevealed = ref(false)

async function loadSample() {
  try {
    const res = await fetch('/demo/6r21-round-sample.json')
    if (!res.ok) throw new Error(String(res.status))
    sample.value = await res.json()
  } catch (e) {
    loadError.value = true
  }
}
function revealEverything(root) {
  for (const element of root.querySelectorAll('[data-reveal]')) element.classList.add('is-visible')
}

function startCoachPreview(reduce = false) {
  if (coachPreviewStarted) return
  coachPreviewStarted = true
  if (reduce) {
    coachPreviewCount.value = 10
    coachExamplesRevealed.value = true
    return
  }
  coachPreviewTimer = window.setInterval(() => {
    coachPreviewCount.value += 1
    if (coachPreviewCount.value < 10) return
    window.clearInterval(coachPreviewTimer)
    coachPreviewTimer = 0
    coachRevealTimer = window.setTimeout(() => {
      coachExamplesRevealed.value = true
      coachRevealTimer = 0
    }, 240)
  }, 180)
}

function releaseHeroIntro(root) {
  // 至少保留两个完整绘制帧，让浏览器先提交隐藏态，再进入 staggered 可见态。
  // 否则首屏较快时，IntersectionObserver 可能与 motion-ready 同帧完成，动效会被吞掉。
  introPrepareFrame = requestAnimationFrame(() => {
    introPlayFrame = requestAnimationFrame(() => {
      root.classList.remove('hero-intro-pending')
      introPlayFrame = 0
    })
    introPrepareFrame = 0
  })
}

function initMotion() {
  const root = landingRoot.value
  if (!root) return
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  root.classList.add('motion-ready')
  if (reduce || typeof IntersectionObserver === 'undefined') {
    revealEverything(root)
    root.classList.remove('hero-intro-pending')
    startCoachPreview(reduce)
    return
  }
  revealObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue
      entry.target.classList.add('is-visible')
      if (entry.target.classList.contains('coach-visual')) startCoachPreview(false)
      revealObserver?.unobserve(entry.target)
    }
  }, { threshold: 0.14, rootMargin: '0px 0px -8% 0px' })
  for (const element of root.querySelectorAll('[data-reveal]')) {
    // 首屏文案是一个完整叙事单元：即使较矮桌面把最后一行压到折叠线下，
    // 也要随同一 stagger 序列收尾，不能永久停在 opacity: 0 等待滚动。
    if (element.closest('.hero-copy')) element.classList.add('is-visible')
    else revealObserver.observe(element)
  }
  releaseHeroIntro(root)
}

function moveStage(event) {
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || !window.matchMedia?.('(pointer: fine)').matches) return
  const host = event.currentTarget
  const rect = host.getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width - 0.5) * 7
  const y = ((event.clientY - rect.top) / rect.height - 0.5) * 7
  cancelAnimationFrame(stageMotionFrame)
  stageMotionFrame = requestAnimationFrame(() => {
    host.style.setProperty('--stage-shift-x', `${x.toFixed(2)}px`)
    host.style.setProperty('--stage-shift-y', `${y.toFixed(2)}px`)
  })
}

function resetStage(event) {
  event.currentTarget.style.setProperty('--stage-shift-x', '0px')
  event.currentTarget.style.setProperty('--stage-shift-y', '0px')
}

onMounted(() => {
  loadSample()
  initMotion()
})

onBeforeUnmount(() => {
  revealObserver?.disconnect()
  cancelAnimationFrame(stageMotionFrame)
  if (coachPreviewTimer) window.clearInterval(coachPreviewTimer)
  if (coachRevealTimer) window.clearTimeout(coachRevealTimer)
  if (introPrepareFrame) cancelAnimationFrame(introPrepareFrame)
  if (introPlayFrame) cancelAnimationFrame(introPlayFrame)
})

// 真实事件时间轴（从样本派生，供「真实事件」行）
const eventTimes = computed(() => {
  const s = sample.value
  if (!s) return []
  const times = []
  for (const k of s.events?.kills || []) if (k.time != null) times.push({ t: k.time, kind: 'kill' })
  for (const tr of s.events?.trades || []) if (tr.time != null) times.push({ t: tr.time, kind: 'trade' })
  for (const b of s.events?.blinds || []) if (b.time != null) times.push({ t: b.time, kind: 'blind' })
  return times.sort((a, b) => a.t - b.t).slice(0, 8)
})

const hasKill = computed(() => (sample.value?.events?.kills?.length ?? 0) > 0)
const hasTrade = computed(() => (sample.value?.events?.trades?.length ?? 0) > 0)

// 6R21C：真实事件计数 + 事件时刻标注（供「01 真实事件」连续叙事卡）
const eventCounts = computed(() => {
  const s = sample.value
  if (!s) return { kill: 0, trade: 0, blind: 0 }
  return {
    kill: s.events?.kills?.length || 0,
    trade: s.events?.trades?.length || 0,
    blind: s.events?.blinds?.length || 0,
  }
})

// F1：长期教练区诚实状态 —— 当前 Landing 用单个真实回合样本做展示，尚未绑定 Steam 聚合场次。
// 绝不把演示样本伪装成 10/30 场满样本；明确标出"样本不足/低置信"，并给出可执行的绑定入口。
const coachSample = computed(() => {
  return {
    status: 'insufficient',
    statusText: '样本不足 · 低置信',
    note: '当前为单回合演示数据；绑定 Steam 采集满 10 场后才会给出可验证趋势。',
  }
})
</script>

<template>
  <div ref="landingRoot" class="landing hero-intro-pending" data-testid="marketing-home">
    <header class="landing-nav">
      <a class="landing-brand" href="#top" aria-label="比赛复盘首页">
        <span class="landing-brand-mark" aria-hidden="true"></span>
        <span>比赛复盘</span>
      </a>
      <nav class="landing-links" aria-label="首页导航">
        <a href="#replay">产品</a>
        <a href="#evidence">功能</a>
        <a href="#coach">教练方案</a>
        <a href="#privacy">数据说明</a>
      </nav>
      <div class="landing-actions">
        <template v-if="authed">
          <router-link class="btn btn-primary landing-register" to="/">进入工作台</router-link>
        </template>
        <template v-else>
          <router-link class="landing-login" to="/login">登录</router-link>
          <router-link class="btn btn-primary landing-register" to="/login?mode=register">免费开始</router-link>
        </template>
      </div>
    </header>

    <main id="top">
      <section id="replay" class="hero" aria-labelledby="hero-title">
        <div class="hero-copy">
          <p class="hero-kicker" data-reveal style="--reveal-order: 0">CS2 赛后复盘 · 真实事件驱动</p>
          <h1 id="hero-title" data-reveal style="--reveal-order: 1">看见这一回合，<br><span>为什么会输。</span></h1>
          <p class="hero-lead" data-reveal style="--reveal-order: 2">把枪线、道具、击杀与区域控制放回同一条时间轴；答案不再藏在比分里。</p>
          <div class="hero-cta" data-reveal style="--reveal-order: 3">
            <a class="btn btn-primary hero-primary" href="#stage">
              打开真实回放
              <MorphIcon :icon="ArrowRight" :size="18" :stroke-width="2" reduced-motion="user" />
            </a>
            <router-link class="btn hero-secondary" :to="authed ? '/' : '/login?mode=register'">
              {{ authed ? '进入我的复盘' : '上传第一场比赛' }}
            </router-link>
          </div>
          <dl class="hero-facts" aria-label="核心能力" data-reveal style="--reveal-order: 4">
            <div><dt>逐秒事件还原</dt><dd>击杀、投掷物与致盲同轴</dd></div>
            <div><dt>区域控制可视化</dt><dd>看见空间优势如何变化</dd></div>
            <div><dt>证据直达回放</dt><dd>结论能够定位与复查</dd></div>
          </dl>
          <p class="map-support" data-reveal style="--reveal-order: 5">地图雷达与导航覆盖 10 张标准对战图；真实区域控制回放：Mirage · Dust II · Inferno</p>
        </div>

        <div id="stage" class="hero-stage-wrap" data-reveal style="--reveal-order: 2" @pointermove="moveStage" @pointerleave="resetStage">
          <LandingStage v-if="sample" :sample="sample" autoplay />
          <div v-else class="stage-loading" data-testid="landing-replay-stage">
            <span v-if="loadError">回合样本暂不可用</span>
            <span v-else>正在装载真实回合样本…</span>
          </div>
        </div>
      </section>

      <section id="evidence" class="proof-flow section-shell" aria-labelledby="proof-title">
        <div class="section-intro" data-reveal>
          <p class="section-index">复盘三步</p>
          <h2 id="proof-title">从发生了什么，到下一次怎么做</h2>
        </div>
        <article class="proof-row proof-event" data-reveal style="--reveal-order: 0">
          <div class="proof-number">01</div>
          <div><p class="proof-eyebrow">真实事件</p><h3>让每个结论都有时间和位置</h3></div>
          <div class="mini-timeline" aria-hidden="true">
            <span class="tl-rail"></span>
            <i v-for="(e, i) in eventTimes" :key="i" :class="['tl-dot', e.kind]" :style="{ left: `${(e.t / (sample?.round?.max_duration || 1)) * 100}%`, '--event-order': i }"></i>
            <div class="evt-legend">
              <span><i class="el-dot kill"></i>击杀 {{ eventCounts.kill }}</span>
              <span><i class="el-dot trade"></i>补枪 {{ eventCounts.trade }}</span>
              <span><i class="el-dot blind"></i>致盲 {{ eventCounts.blind }}</span>
            </div>
          </div>
          <p>击杀、开火、受伤、投掷与致盲事件自动对齐，拒绝用结果倒推过程。</p>
        </article>
        <article class="proof-row proof-space" data-reveal style="--reveal-order: 1">
          <div class="proof-number">02</div>
          <div><p class="proof-eyebrow">回合证据</p><h3>把空间优势画在真实地图上</h3></div>
          <LandingControlPreview v-if="sample" :sample="sample" />
          <div v-else class="control-preview-loading">正在装载真实地图证据…</div>
          <p>区域状态与时间轴同步；低覆盖时明确降级，不把停留误说成控制。</p>
        </article>
        <article class="proof-row proof-action" data-reveal style="--reveal-order: 2">
          <div class="proof-number">03</div>
          <div><p class="proof-eyebrow">教练建议</p><h3>把一场比赛变成可执行训练</h3></div>
          <div class="action-chain"><span>定位问题</span><MorphIcon :icon="ArrowRight" :size="17" /><span>打开证据</span><MorphIcon :icon="ArrowRight" :size="17" /><span>下场验证</span></div>
          <p>只从可信快照生成建议，并让你能回到那一秒亲自判断。</p>
        </article>
      </section>

      <section id="coach" class="coach-section section-shell" aria-labelledby="coach-title">
        <div class="coach-copy" data-reveal>
          <p class="section-index">长期教练</p>
          <h2 id="coach-title">它不只复盘这一场。<br>它会慢慢认识你。</h2>
          <p>绑定 Steam 身份后，把最近 10 / 30 场连接成一份可验证的成长档案：重复问题、趋势变化，以及最值得改的三件事。</p>
          <router-link class="text-link" :to="authed ? '/coach' : '/login?mode=register'">{{ authed ? '打开长期教练' : '建立我的长期档案' }} <MorphIcon :icon="ArrowRight" :size="17" /></router-link>
        </div>
        <div class="coach-visual" data-reveal style="--reveal-order: 1">
          <div class="cv-head">
            <span>长期档案 · 每场可改进机会</span>
            <span class="cv-sample-badge" data-testid="coach-sample-status">{{ coachSample.statusText }}</span>
          </div>
          <div class="cv-method">
            <p><strong>每场可改进机会</strong> = 带回放证据的训练问题触发次数 ÷ 达到最低解析覆盖率的有效比赛场数。一次机会必须能定位到具体回合与时刻；无证据、未完成解析或低覆盖片段不计入。</p>
          </div>
          <section
            class="cv-readiness"
            role="img"
            :aria-label="`长期档案解锁演示进度 ${coachPreviewCount}/10；当前真实样本仍为 0 场有效比赛。10 场解锁可信趋势和区间，30 场用于验证重复性，每条 Top 3 结论必须带可点击证据`"
            data-testid="coach-readiness"
          >
            <div class="cv-readiness-main">
              <p class="cv-readiness-kicker">解锁演示 · 当前真实样本 0 场</p>
              <p class="cv-readiness-count"><strong :key="coachPreviewCount">{{ coachPreviewCount }}</strong><span>/ 10 场</span></p>
              <p class="cv-readiness-copy">动画仅演示解锁流程；当前单回合样本不计入有效比赛。</p>
              <progress :value="coachPreviewCount" max="10" :aria-label="`解锁演示进度：${coachPreviewCount}/10 场；当前真实样本 0 场`">{{ coachPreviewCount }} / 10</progress>
              <div class="cv-readiness-scale" aria-hidden="true"><span>演示开始</span><span>可信趋势门槛</span></div>
            </div>
            <ol class="cv-milestones" aria-label="长期档案解锁条件">
              <li><span>10 场</span><div><strong>可信趋势与区间</strong><small>样本和解析覆盖率同时达标</small></div><em>待解锁</em></li>
              <li><span>30 场</span><div><strong>验证重复问题</strong><small>区分偶发失误与稳定模式</small></div><em>待解锁</em></li>
              <li><span>证据</span><div><strong>Top 3 可点击结论</strong><small>每条都能回到具体回合与时刻</small></div><em>必需</em></li>
            </ol>
          </section>
          <ol class="cv-list" :class="{ 'is-revealed': coachExamplesRevealed }" :aria-hidden="!coachExamplesRevealed" data-testid="coach-examples">
            <li style="--example-order: 0"><b>01</b><div><strong>中路第一颗烟后的停顿</strong><span>绑定后按真实场次统计重复与置信</span></div><em>示例</em></li>
            <li style="--example-order: 1"><b>02</b><div><strong>被首杀后的补枪距离</strong><span>绑定后按真实场次统计重复与置信</span></div><em>示例</em></li>
            <li style="--example-order: 2"><b>03</b><div><strong>转点前的信息确认</strong><span>绑定后按真实场次统计重复与置信</span></div><em>示例</em></li>
          </ol>
          <p class="cv-note" data-testid="coach-sample-note">{{ coachSample.note }}</p>
        </div>
      </section>

      <section id="privacy" class="privacy-strip section-shell" data-reveal>
        <div><p class="section-index">你的数据，你做主</p><h2>私人复盘，不是公开战绩墙。</h2></div>
        <p>录像仅用于你的复盘分析。身份、个人画像与团队权限按绑定关系隔离；分享内容会经过脱敏和权限检查。</p>
        <router-link class="btn btn-primary" :to="authed ? '/' : '/login?mode=register'">{{ authed ? '进入工作台' : '免费开始' }}</router-link>
      </section>
    </main>

    <footer class="landing-footer">
      <span>比赛复盘</span><span>从真实 Demo 开始，留下进步的证据。</span>
    </footer>
  </div>
</template>

<style scoped>
.landing {
  --landing-max: 1460px;
  min-height: 100vh;
  color: var(--text-primary);
  background:
    radial-gradient(1200px 600px at 76% -10%, rgba(230,165,74,.07), transparent 60%),
    radial-gradient(900px 500px at 6% 20%, rgba(91,157,217,.05), transparent 55%),
    #090c10;
  overflow-x: clip;
}
.landing-nav { height: 62px; max-width: var(--landing-max); margin: 0 auto; padding: 0 34px; display: flex; align-items: center; gap: 54px; border-bottom: 1px solid rgba(51,61,74,.62); }
.motion-ready .landing-nav { animation: navEnter 360ms var(--ease) both; }
.landing-brand { display: inline-flex; align-items: center; gap: 11px; color: var(--text-primary); font-weight: 800; font-size: 17px; letter-spacing: .04em; }
.landing-brand-mark { width: 12px; height: 12px; border-radius: 2px; background: var(--accent); transform: rotate(45deg); box-shadow: 0 0 14px rgba(230,165,74,.5); }
.landing-links { display: flex; gap: 34px; }
.landing-links a, .landing-login { color: var(--text-secondary); font-size: 13px; }
.landing-links a:hover, .landing-login:hover { color: var(--text-primary); }
.landing-actions { margin-left: auto; display: flex; align-items: center; gap: 18px; }
.landing-register { min-height: 38px; }

.hero { max-width: var(--landing-max); margin: 0 auto; display: grid; grid-template-columns: minmax(440px,.92fr) minmax(640px,1.2fr); gap: 40px; min-height: 690px; border-bottom: 1px solid var(--border); align-items: center; padding: 42px 34px 46px; }
.hero-copy { position: relative; padding: 20px 22px 20px 30px; display: flex; flex-direction: column; align-items: flex-start; }
/* F1（批注5）标题区视觉承接：左侧一条琥珀竖线，把"标题→舞台"连成一体，不再让文案孤悬 */
.hero-copy::before { content: ''; position: absolute; left: 0; top: 30px; bottom: 46px; width: 3px; background: linear-gradient(180deg, var(--accent) 0%, rgba(230,165,74,.16) 100%); border-radius: 3px; }
.hero-copy::after { content: ''; position: absolute; width: 150px; height: 150px; right: -40px; bottom: -64px; border: 1px solid rgba(230,165,74,.12); transform: rotate(45deg); pointer-events: none; }
.hero-kicker { color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: .16em; }
.hero h1 { margin-top: 25px; font-size: var(--mark-display); line-height: var(--mark-display-lh); letter-spacing: -.055em; font-weight: 900; }
.hero h1 span { color: var(--accent-strong); }
.hero-lead { max-width: 520px; margin-top: 27px; color: #aab4c2; font-size: 16px; line-height: 1.95; }
.hero-cta { display: flex; gap: 12px; margin-top: 28px; flex-wrap: wrap; }
.hero-primary, .hero-secondary { min-height: 50px; padding: 0 22px; }
.hero-primary { box-shadow: 0 10px 26px rgba(230,165,74,.18); }
.hero-secondary { background: rgba(12,16,22,.6); }
.hero-facts { margin-top: 30px; display: grid; grid-template-columns: 1fr; gap: 13px; width: 100%; }
.hero-facts div { display: grid; grid-template-columns: 132px 1fr; gap: 14px; align-items: baseline; padding-top: 11px; border-top: 1px solid rgba(51,61,74,.5); }
.hero-facts dt { color: var(--text-primary); font-size: 13px; font-weight: 700; }
.hero-facts dd { color: var(--text-muted); font-size: 12px; }
.map-support { margin-top: auto; padding-top: 26px; color: var(--text-muted); font-size: 11px; letter-spacing: .03em; }

.hero-stage-wrap { min-width: 0; --stage-shift-x: 0px; --stage-shift-y: 0px; transform: translate3d(var(--stage-shift-x), var(--stage-shift-y), 0); transition: transform 360ms var(--ease); will-change: transform; }
.stage-loading { aspect-ratio: 1; box-sizing: content-box; padding-block: 77px; display: grid; place-items: center; border: 1px solid var(--border-strong); background: #0a0f15; color: var(--text-muted); font-size: 13px; }

.section-shell { max-width: var(--landing-max); margin: 0 auto; padding: 72px 64px; }
.proof-flow, .coach-section, .privacy-strip { content-visibility: auto; }
.proof-flow { contain-intrinsic-size: auto 620px; }
.coach-section { contain-intrinsic-size: auto 760px; }
.privacy-strip { contain-intrinsic-size: auto 260px; }
.section-intro { display: grid; grid-template-columns: 150px 1fr; align-items: end; margin-bottom: 28px; }
.section-index { color: var(--accent); font-size: 11px; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; }
.section-intro h2, .coach-copy h2, .privacy-strip h2 { font-size: var(--mark-title); line-height: var(--mark-title-lh); letter-spacing: -.025em; }
.proof-row { display: grid; grid-template-columns: 70px minmax(220px,.9fr) minmax(290px,1.2fr) minmax(260px,1fr); gap: 28px; align-items: center; min-height: 126px; border-top: 1px solid var(--border-strong); }
.proof-row:last-child { border-bottom: 1px solid var(--border-strong); }
.proof-number { color: var(--text-faint); font-size: 30px; font-weight: 300; }
.proof-eyebrow { color: var(--text-muted); font-size: 11px; letter-spacing: .12em; }
.proof-row h3 { margin-top: 4px; font-size: 18px; }
.proof-row > p { color: var(--text-secondary); font-size: 13px; line-height: 1.75; }
.mini-timeline { height: 60px; position: relative; }
.tl-rail { position: absolute; left: 0; right: 0; top: 20px; height: 1px; background: var(--border-strong); }
.tl-dot { position: absolute; top: 20px; width: 9px; height: 9px; transform: translate(-50%,-50%) rotate(45deg); border-radius: 2px; }
.tl-dot.kill { background: var(--err); box-shadow: 0 0 0 5px rgba(229,100,93,.12); }
.tl-dot.trade { background: var(--ok); box-shadow: 0 0 0 5px rgba(87,184,119,.12); }
.tl-dot.blind { background: var(--info); box-shadow: 0 0 0 5px rgba(91,157,217,.12); }
.evt-legend { position: absolute; left: 0; right: 0; bottom: 4px; display: flex; gap: 16px; align-items: center; }
.evt-legend span { display: inline-flex; align-items: center; gap: 6px; color: var(--text-muted); font-size: 11px; }
.el-dot { width: 8px; height: 8px; border-radius: 2px; transform: rotate(45deg); display: inline-block; }
.el-dot.kill { background: var(--err); }
.el-dot.trade { background: var(--ok); }
.el-dot.blind { background: var(--info); }
.control-preview-loading { min-height: 136px; display: grid; place-items: center; border: 1px solid var(--border); color: var(--text-muted); font-size: 10px; }
.action-chain { display: flex; align-items: center; gap: 9px; color: var(--text-secondary); font-size: 11px; flex-wrap: wrap; }
.action-chain svg { color: var(--accent); }

.coach-section { display: grid; grid-template-columns: .82fr 1.18fr; gap: 80px; background: linear-gradient(180deg, #0d1117, #0a0e13); border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.coach-copy h2 { margin-top: 14px; }
.coach-copy > p:not(.section-index) { margin-top: 20px; color: var(--text-secondary); max-width: 520px; line-height: 1.85; }
.text-link { margin-top: 24px; display: inline-flex; align-items: center; gap: 8px; color: var(--accent); font-weight: 700; }
.coach-visual { border: 1px solid var(--border-strong); background: #0a0e13; }
.cv-head { height: 54px; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; border-bottom: 1px solid var(--border); }
.cv-head > span:first-child { font-weight: 800; }
.cv-sample-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 10px; font-weight: 700; padding: 2px 9px; border-radius: var(--radius-pill); color: var(--warn); background: var(--warn-soft); white-space: nowrap; }
.cv-method { padding: 12px 20px; border-bottom: 1px solid var(--border); }
.cv-method p { margin: 0; color: var(--text-muted); font-size: 11px; line-height: 1.7; }
.cv-method strong { color: var(--text-secondary); }
.cv-readiness { display: grid; grid-template-columns: minmax(190px,.78fr) minmax(250px,1.22fr); border-bottom: 1px solid var(--border); background: linear-gradient(135deg, rgba(230,165,74,.055), transparent 46%); }
.cv-readiness-main { padding: 22px 20px 20px; border-right: 1px solid var(--border); }
.cv-readiness-kicker { margin: 0 0 10px; color: var(--accent); font-size: 10px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.cv-readiness-count { display: flex; align-items: baseline; gap: 7px; margin: 0; }
.cv-readiness-count strong { color: var(--text-primary); font-size: clamp(42px,5vw,58px); line-height: .92; letter-spacing: -.06em; }
.coach-visual.is-visible .cv-readiness-count strong { animation: countStep 180ms var(--ease) both; }
.cv-readiness-count span { color: var(--text-secondary); font-size: 14px; font-weight: 750; }
.cv-readiness-copy { min-height: 34px; margin: 12px 0 16px; color: var(--text-muted); font-size: 10px; line-height: 1.65; }
.cv-readiness progress { display: block; width: 100%; height: 6px; border: 0; border-radius: var(--radius-pill); overflow: hidden; background: rgba(152,164,179,.12); }
.cv-readiness progress::-webkit-progress-bar { background: rgba(152,164,179,.12); border-radius: var(--radius-pill); }
.cv-readiness progress::-webkit-progress-value { background: linear-gradient(90deg, var(--accent), var(--accent-strong)); border-radius: var(--radius-pill); transition: width 180ms linear; }
.cv-readiness progress::-moz-progress-bar { background: linear-gradient(90deg, var(--accent), var(--accent-strong)); border-radius: var(--radius-pill); transition: width 180ms linear; }
.cv-readiness-scale { display: flex; justify-content: space-between; margin-top: 7px; color: var(--text-faint); font-size: 9px; }
.cv-milestones { list-style: none; margin: 0; padding: 0; }
.cv-milestones li { min-height: 62px; display: grid; grid-template-columns: 44px 1fr auto; gap: 12px; align-items: center; padding: 11px 18px; border-top: 1px solid var(--border); }
.cv-milestones li:first-child { border-top: 0; }
.cv-milestones li > span { color: var(--accent); font-size: 11px; font-weight: 800; }
.cv-milestones li div { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cv-milestones strong { color: var(--text-secondary); font-size: 11px; }
.cv-milestones small { color: var(--text-faint); font-size: 9px; line-height: 1.45; }
.cv-milestones em { color: var(--text-faint); font-size: 9px; font-style: normal; white-space: nowrap; }
.cv-list { list-style: none; margin: 0; max-height: 0; opacity: 0; overflow: hidden; transform: translateY(-8px); transition: max-height 360ms var(--ease), opacity 260ms var(--ease), transform 360ms var(--ease); }
.cv-list.is-revealed { max-height: 260px; opacity: 1; transform: none; }
.cv-list li { display: grid; grid-template-columns: 34px 1fr auto; gap: 12px; align-items: center; padding: 16px 20px; border-top: 1px solid var(--border); }
.motion-ready .cv-list li { opacity: 0; transform: translateY(-8px); }
.motion-ready .cv-list.is-revealed li { animation: exampleUnfold 260ms var(--ease) both; animation-delay: calc(var(--example-order, 0) * 70ms); }
.cv-list li > b { color: var(--text-faint); font-size: 16px; }
.cv-list li div { display: flex; flex-direction: column; gap: 3px; }
.cv-list strong { font-size: 13px; }
.cv-list span { color: var(--text-muted); font-size: 11px; }
.cv-list em { color: var(--accent); font-size: 10px; font-style: normal; }
.cv-note { margin: 0; padding: 12px 20px; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 11px; line-height: 1.6; }

.privacy-strip { display: grid; grid-template-columns: 1fr 1fr auto; gap: 50px; align-items: center; }
.privacy-strip h2 { margin-top: 10px; font-size: 27px; }
.privacy-strip > p { color: var(--text-secondary); font-size: 13px; line-height: 1.75; }
.landing-footer { max-width: var(--landing-max); margin: 0 auto; min-height: 78px; display: flex; justify-content: space-between; align-items: center; padding: 0 64px; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 11px; }
.landing-footer span:first-child { color: var(--text-primary); font-weight: 800; }

/* Detail 式微交互 + MotionSites 式分段入场：内容可读优先，进入一次后不循环打扰。 */
.motion-ready [data-reveal] {
  opacity: 0;
  transform: translate3d(0, 22px, 0);
  transition:
    opacity 400ms var(--ease),
    transform 400ms var(--ease),
    border-color var(--dur-fast) var(--ease),
    background-color var(--dur-fast) var(--ease),
    box-shadow var(--dur-mid) var(--ease);
  transition-delay: calc(var(--reveal-order, 0) * 70ms);
}
.motion-ready [data-reveal].is-visible { opacity: 1; transform: translate3d(0, 0, 0); }
.hero-intro-pending .hero-copy [data-reveal],
.motion-ready.hero-intro-pending .hero-copy [data-reveal].is-visible {
  opacity: 0;
  transform: translate3d(0, 22px, 0);
}
.motion-ready .hero-stage-wrap[data-reveal] { transform: translate3d(var(--stage-shift-x), calc(18px + var(--stage-shift-y)), 0) scale(.985); }
.motion-ready .hero-stage-wrap[data-reveal].is-visible { transform: translate3d(var(--stage-shift-x), var(--stage-shift-y), 0) scale(1); }
.hero h1 span { background: linear-gradient(90deg, var(--accent-strong), #ffd28b, var(--accent-strong)); background-size: 200% 100%; background-clip: text; -webkit-background-clip: text; color: transparent; }
.motion-ready .hero h1.is-visible span { animation: titleSheen 400ms 360ms var(--ease) both; }
.motion-ready .proof-event .tl-rail { transform: scaleX(0); transform-origin: left center; }
.motion-ready .proof-event.is-visible .tl-rail { animation: railDraw 400ms 120ms var(--ease) both; }
.motion-ready .proof-event .tl-dot { opacity: 0; }
.motion-ready .proof-event.is-visible .tl-dot { animation: eventArrive 240ms var(--ease) both; animation-delay: calc(190ms + var(--event-order, 0) * 70ms); }
.motion-ready .proof-event .evt-legend { opacity: 0; transform: translateY(5px); }
.motion-ready .proof-event.is-visible .evt-legend { animation: legendArrive 280ms 720ms var(--ease) both; }
.hero-primary svg, .text-link svg, .action-chain svg { transition: transform var(--dur-fast) var(--ease); }
.hero-primary:hover svg, .hero-primary:focus-visible svg, .text-link:hover svg, .text-link:focus-visible svg { transform: translateX(4px); }

@media (hover: hover) {
  .proof-row:hover { background: rgba(255,255,255,.018); border-color: rgba(230,165,74,.32); box-shadow: inset 3px 0 0 rgba(230,165,74,.7); }
  .proof-row:hover .proof-number { color: var(--accent); transform: translateX(4px); }
  .proof-number { transition: color var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease); }
  .coach-visual:hover { border-color: rgba(230,165,74,.38); box-shadow: 0 18px 46px rgba(0,0,0,.22); }
}

@keyframes navEnter { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: none; } }
@keyframes titleSheen { from { background-position: 100% 0; } to { background-position: 0 0; } }
@keyframes railDraw { to { transform: scaleX(1); } }
@keyframes eventArrive { from { opacity: 0; transform: translate(-50%,-50%) rotate(45deg) scale(.25); } to { opacity: 1; transform: translate(-50%,-50%) rotate(45deg) scale(1); } }
@keyframes legendArrive { to { opacity: 1; transform: none; } }
@keyframes countStep { from { opacity: .45; transform: translateY(5px); } to { opacity: 1; transform: none; } }
@keyframes exampleUnfold { to { opacity: 1; transform: none; } }

@media (max-width: 1100px) {
  .hero { grid-template-columns: 1fr; padding: 42px 22px; gap: 24px; }
  .hero-copy { padding: 16px 10px 0; }
  .hero-copy::after { display: none; }
  .proof-row { grid-template-columns: 54px 1fr 1fr; }
  .proof-row > p { grid-column: 2 / -1; margin-top: -20px; padding-bottom: 18px; }
  .coach-section { gap: 38px; }
}
@media (max-width: 760px) {
  .landing-nav { height: 58px; padding: 0 16px; gap: 14px; }
  .landing-links { display: none; }
  .landing-login { display: none; }
  .landing-register { min-height: 44px; padding: 0 14px; }
  .hero { min-height: 0; padding: 34px 14px 8px; }
  .hero-copy { padding: 6px 4px 0; }
  .hero h1 { font-size: clamp(42px,13vw,58px); }
  .hero-lead { font-size: 15px; }
  .hero-cta { width: 100%; }
  .hero-primary, .hero-secondary { flex: 1 1 100%; }
  .hero-facts div { grid-template-columns: 1fr; gap: 2px; }
  .hero-stage-wrap { margin: 22px 0 0; }
  .stage-loading { padding-block: 83px; }
  .section-shell { padding: 54px 18px; }
  .section-intro { grid-template-columns: 1fr; gap: 8px; }
  .section-intro h2 { font-size: 29px; }
  .proof-row { grid-template-columns: 42px 1fr; gap: 16px; padding: 24px 0; }
  .proof-row > :nth-child(3), .proof-row > p { grid-column: 2; margin: 0; }
  .proof-row > p { padding: 0; }
  .control-preview-loading { width: 100%; }
  .coach-section { grid-template-columns: 1fr; gap: 34px; }
  .cv-readiness { grid-template-columns: 1fr; }
  .cv-readiness-main { border-right: 0; border-bottom: 1px solid var(--border); }
  .privacy-strip { grid-template-columns: 1fr; gap: 22px; }
  .privacy-strip .btn { width: 100%; }
  .landing-footer { padding: 0 18px; flex-direction: column; justify-content: center; gap: 4px; align-items: flex-start; }
}
@media (max-width: 374px) {
  /* Keep the intentional two-line headline intact on 360px phones. */
  .hero h1 { font-size: clamp(38px, 11.3vw, 41px); }
}
@media (prefers-reduced-motion: reduce) {
  .motion-ready .landing-nav,
  .motion-ready .hero h1.is-visible span,
  .motion-ready .proof-event.is-visible .tl-rail,
  .motion-ready .proof-event.is-visible .tl-dot,
  .motion-ready .proof-event.is-visible .evt-legend,
  .coach-visual.is-visible .cv-readiness-count strong,
  .motion-ready .cv-list.is-revealed li { animation: none; }
  .motion-ready [data-reveal],
  .motion-ready [data-reveal].is-visible,
  .motion-ready .hero-stage-wrap[data-reveal],
  .motion-ready .hero-stage-wrap[data-reveal].is-visible {
    opacity: 1;
    transform: none;
    transition: none;
  }
  .hero-intro-pending .hero-copy [data-reveal],
  .motion-ready.hero-intro-pending .hero-copy [data-reveal].is-visible {
    opacity: 1;
    transform: none;
  }
  .hero-stage-wrap { transform: none; transition: none; }
  .motion-ready .proof-event .tl-rail { transform: none; }
  .motion-ready .proof-event .tl-dot,
  .motion-ready .proof-event .evt-legend,
  .motion-ready .cv-list li { opacity: 1; transform: none; }
  .cv-list { transition: none; }
}
</style>
