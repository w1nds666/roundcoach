<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api/client'

const router = useRouter()
const dragging = ref(false)
const file = ref(null)          // 待上传文件
const phase = ref('idle')       // idle | uploading | queued | parsing | parsed | failed
const progress = ref(0)         // 上传百分比 0-100（真实 XHR 进度）
const statusText = ref('')
const error = ref('')           // 展示给用户的错误（含中文）
const duplicateDeleted = ref(false)
const demoId = ref(null)
const fileInput = ref(null)
const batchFiles = ref([])
const batchResults = ref(null)
const batchUploading = ref(false)
let disposed = false

const MAX_BYTES = 300 * 1024 * 1024   // 与后端配置一致（默认 300MiB，后端为最终权威）
const MAX_LABEL = '300MiB'

const canPick = computed(() => !batchUploading.value && (phase.value === 'idle' || phase.value === 'failed'))
const sizeLabel = computed(() => file.value ? fmtSize(file.value.size) : '')

function fmtSize(n) {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}

function pick(f) {
  if (!f) return
  file.value = null
  batchFiles.value = []; batchResults.value = null
  error.value = ''
  duplicateDeleted.value = false
  // 前端校验：扩展名 + 大小（后端仍为最终权威）
  if (!f.name.toLowerCase().endsWith('.dem')) {
    error.value = '仅支持 .dem 文件（CS2 比赛录像）'
    return
  }
  if (f.size > MAX_BYTES) {
    error.value = `文件超过 ${MAX_LABEL} 上限（当前 ${fmtSize(f.size)}）`
    return
  }
  file.value = f
}

function onDrop(e) {
  if (!canPick.value) return
  dragging.value = false
  const selected = [...(e.dataTransfer?.files || [])]
  if (selected.length > 1) selectBatch(selected)
  else pick(selected[0])
}
function onSelect(e) {
  if (!canPick.value) { e.target.value = ''; return }
  const selected = [...(e.target.files || [])]
  if (selected.length > 1) selectBatch(selected)
  else pick(selected[0])
  e.target.value = ''   // 允许重复选择同一文件
}
function selectBatch(files) {
  if (!canPick.value) return
  file.value = null; phase.value = 'idle'; error.value = ''; batchResults.value = null; batchFiles.value = files
}
function onDropKey(e) {
  if (!canPick.value) return
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.value?.click() }
}

async function startBatch() {
  if (disposed || !batchFiles.value.length || batchUploading.value) return
  if (batchFiles.value.length > 20) { error.value = '批量最多选择 20 个 .dem 文件，请减少后再上传'; return }
  batchUploading.value = true; error.value = ''; batchResults.value = null
  try {
    const result = await api.uploadBatch('/api/demos/upload/batch', batchFiles.value)
    if (!disposed) batchResults.value = result
  }
  catch (e) { if (!disposed) error.value = e instanceof ApiError ? uploadErrorText(e) : '批量上传失败，请重试' }
  finally { if (!disposed) batchUploading.value = false }
}

async function startUpload() {
  if (disposed || !file.value || phase.value === 'uploading') return
  error.value = ''
  progress.value = 0
  phase.value = 'uploading'
  statusText.value = '上传中…'
  try {
    const resp = await api.upload('/api/demos/upload', file.value, (p) => { if (!disposed) progress.value = p })
    if (disposed) return
    demoId.value = resp.demo_id
    phase.value = 'queued'
    statusText.value = '已进入解析队列…'
    startPolling()
  } catch (e) {
    if (disposed) return
    phase.value = 'failed'
    statusText.value = '上传失败'
    progress.value = 0
    if (e instanceof ApiError) {
      duplicateDeleted.value = e.code === 'DUPLICATE_DEMO' && !!e.details?.existing_deleted
      error.value = uploadErrorText(e)
    } else {
      error.value = '网络异常，请检查连接后重试'
    }
  }
}

function uploadErrorText(e) {
  switch (e.code) {
    case 'DUPLICATE_DEMO': return e.details?.existing_deleted ? '该 Demo 在回收站，可前往回收站恢复。' : `内容已上传（Demo #${e.details?.existing_demo_id || '已有记录'}）。`
    case 'FREE_DAILY_LIMIT': return '今日免费上传额度已用完（每天 1 个）。升级进阶版可解锁更多额度。'
    case 'FILE_TOO_LARGE': return `文件超过 ${MAX_LABEL} 上限，请压缩或分段后重试。`
    case 'FILE_TYPE_INVALID': return '仅支持 .dem 文件。'
    case 'FILE_CONTENT_INVALID': return '文件内容不是合法的 CS2 demo（缺少 PBDEMS2 文件头）。'
    case 'QUEUE_UNAVAILABLE': return '解析队列暂不可用，请稍后重试。'
    case 'AUTH_REQUIRED': return '登录已失效，请重新登录。'
    default: return e.message || '上传失败，请重试。'
  }
}

// ---- 状态轮询：上传成功后轮询解析状态（每 2s，最多 60 次后停止） ----
let timer = null
let polls = 0
let pollGeneration = 0
const MAX_POLLS = 60

function startPolling() {
  polls = 0
  stopPolling()
  const generation = pollGeneration
  const id = demoId.value
  let inFlight = false
  timer = setInterval(async () => {
    if (generation !== pollGeneration || inFlight) return
    inFlight = true
    polls += 1
    try {
      const st = await api.get(`/api/demos/${id}/status`)
      if (generation !== pollGeneration) return
      if (st.status === 'parsed') {
        phase.value = 'parsed'
        statusText.value = '解析完成'
        stopPolling()
      } else if (st.status === 'failed') {
        phase.value = 'failed'
        statusText.value = '解析失败'
        error.value = st.error_msg ? `解析失败：${st.error_msg}` : '解析失败，请重新上传'
        stopPolling()
      } else if (st.status === 'uploaded') {
        phase.value = 'queued'
        statusText.value = '已进入解析队列…'
      } else if (st.status === 'parsing') {
        phase.value = 'parsing'
        statusText.value = '解析中…'
      }
    } catch {
      // 网络抖动：继续轮询（上限内）；停止后的响应不得更新界面。
    } finally {
      inFlight = false
      if (generation === pollGeneration && polls >= MAX_POLLS) {
        phase.value = 'queued'
        statusText.value = '解析状态查询中断，可到「我的复盘」查看进度'
        stopPolling()
      }
    }
  }, 2000)
}
function stopPolling() { pollGeneration++; if (timer) { clearInterval(timer); timer = null } }
onBeforeUnmount(() => { disposed = true; stopPolling() })

function goReport() { router.push(`/demo/${demoId.value}`) }
function reset() {
  stopPolling()
  phase.value = 'idle'; progress.value = 0; error.value = ''; statusText.value = ''; demoId.value = null; file.value = null; batchFiles.value = []; batchResults.value = null
}
</script>

<template>
  <div class="page-wrap">
    <div class="page-head">
      <div>
        <p class="eyebrow">Upload</p>
        <h1 class="page-title">上传比赛</h1>
        <p class="page-sub">把 .dem 拖进来，剩下的交给我们。上传后自动解析，10-60 秒出结果。</p>
      </div>
      <router-link to="/" class="btn btn-secondary">返回我的复盘</router-link>
    </div>

    <!-- 解析流程 -->
    <div class="flow" aria-label="上传解析流程">
      <div class="flow-step"><span class="flow-no num">1</span><span>拖入 / 选择 .dem 文件</span></div>
      <span class="flow-arrow" aria-hidden="true">→</span>
      <div class="flow-step"><span class="flow-no num">2</span><span>上传并自动解析（10-60 秒）</span></div>
      <span class="flow-arrow" aria-hidden="true">→</span>
      <div class="flow-step"><span class="flow-no num">3</span><span>进入报告逐回合复盘</span></div>
    </div>

    <!-- 拖拽区（键盘可操作、焦点可见） -->
    <div
      class="dropzone"
      :class="{ dragging, disabled: !canPick }"
      role="button"
      :tabindex="canPick ? 0 : -1"
      :aria-disabled="!canPick"
      :aria-label="canPick ? '选择或拖拽 .dem 文件上传' : '当前不可上传'"
      @dragenter.prevent="canPick && (dragging = true)"
      @dragover.prevent="canPick && (dragging = true)"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
      @keydown="onDropKey"
      @click="canPick && fileInput.click()"
    >
      <input ref="fileInput" type="file" accept=".dem" multiple hidden :disabled="!canPick" @change="onSelect" />
      <div v-if="!file && !batchFiles.length" class="dz-empty">
        <p class="dz-main">拖拽 .dem 文件到这里，或 <span class="dz-link">点击选择</span></p>
        <p class="dz-sub">单个文件 ≤ {{ MAX_LABEL }}；免费用户每天 1 个</p>
      </div>
      <div v-else-if="batchFiles.length" class="dz-file">
        <p class="dz-main">已选择 {{ batchFiles.length }} 个 Demo</p>
        <p class="dz-sub">可批量上传，最多 20 个</p>
      </div>
      <div v-else class="dz-file">
        <p class="dz-main ellipsis">{{ file.name }}</p>
        <p class="dz-sub num">{{ sizeLabel }}</p>
      </div>
    </div>

    <!-- 格式 / 大小 / 隐私说明 -->
    <div class="upload-note">
      <span><b>.dem</b> 为 CS2 比赛录像格式（PBDEMS2 文件头）</span>
      <span>单个文件 ≤ {{ MAX_LABEL }}</span>
      <span>解析约需 10-60 秒，队列空闲时更快</span>
    </div>
    <p class="upload-privacy">录像仅用于你本人的复盘分析，保存在你的账户下；单文件 ≤ {{ MAX_LABEL }}，失败可重试，重复文件会提示去回收站恢复。</p>

    <div v-if="batchFiles.length" class="actions">
      <button class="btn btn-primary big" :disabled="batchUploading" @click="startBatch">{{ batchUploading ? '批量处理中…' : '批量上传并解析' }}</button>
      <button class="btn" :disabled="batchUploading" @click="batchFiles = []; batchResults = null">清空</button>
    </div>
    <div v-if="batchResults" class="card batch-results">
      <p class="status">批次完成：{{ batchResults.succeeded }} 成功，{{ batchResults.failed }} 失败</p>
      <div v-for="r in batchResults.results" :key="r.index" class="batch-row">
        <span class="ellipsis">{{ r.file_name }}</span><span v-if="r.ok" class="pill pill-ok">已入队 #{{ r.demo_id }}</span><span v-else-if="r.error?.existing_demo_id" class="pill pill-err">{{ r.error.existing_deleted ? '回收站重复' : '重复' }}：已有 Demo #{{ r.error.existing_demo_id }}</span><span v-else class="pill pill-err">{{ r.error?.message }}</span>
      </div>
      <button v-if="batchResults.results.some((r) => r.error?.existing_deleted)" class="btn btn-ghost mt" @click="router.push('/?view=trash')">打开回收站</button>
      <button class="btn btn-ghost mt" @click="router.push('/')">查看我的复盘</button>
    </div>

    <!-- 错误 -->
    <div v-if="error" class="form-error-box mt" role="alert">{{ error }} <router-link v-if="duplicateDeleted" to="/?view=trash">打开回收站</router-link></div>

    <!-- 上传进度（真实） -->
    <div v-if="phase === 'uploading'" class="card progress-card" aria-live="polite">
      <div class="row">
        <span class="status">{{ statusText }}</span>
        <span class="num">{{ progress }}%</span>
      </div>
      <div class="bar"><div class="bar-fill" :style="{ width: progress + '%' }"></div></div>
    </div>

    <!-- 解析状态（诚实：不确定进度用 indeterminate 指示，非假百分比） -->
    <div v-else-if="phase === 'queued' || phase === 'parsing' || phase === 'parsed' || phase === 'failed'" class="card progress-card" aria-live="polite">
      <div class="row">
        <span class="status">
          <span class="pill" :class="{
            'pill-ok': phase === 'parsed',
            'pill-err': phase === 'failed',
            'pill-info': phase === 'queued' || phase === 'parsing',
          }">{{ statusText }}</span>
        </span>
      </div>
      <template v-if="phase === 'queued' || phase === 'parsing'">
        <div class="bar running"><div class="bar-fill shimmer"></div></div>
        <p class="dz-sub mt">解析约需 10-60 秒，可在「我的复盘」中随时查看进度</p>
      </template>
      <div v-else class="actions">
        <button v-if="phase === 'parsed'" class="btn btn-primary" @click="goReport">查看报告 →</button>
        <button v-if="phase === 'failed'" class="btn" @click="reset">重新上传</button>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div v-if="file && (phase === 'idle' || phase === 'failed')" class="actions">
      <button class="btn btn-primary big" :disabled="phase === 'uploading'" @click="startUpload">
        上传并解析
      </button>
      <button class="btn" @click="reset">取消</button>
    </div>
  </div>
</template>

<style scoped>
.mt { margin-top: 14px; }

.flow { display: flex; align-items: center; gap: 14px; margin-top: 22px; flex-wrap: wrap; }
.flow-step { display: inline-flex; align-items: center; gap: 9px; color: var(--text-secondary); font-size: 13px; }
.flow-no { width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; background: var(--accent-soft); color: var(--accent); font-size: 12px; font-weight: 700; }
.flow-arrow { color: var(--text-faint); }

.dropzone { margin-top: 18px; border: 1.5px dashed var(--border-strong); border-radius: var(--radius-lg); padding: 40px 24px; text-align: center; cursor: pointer; background: var(--bg-panel); transition: border-color var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease); outline: none; }
.dropzone:hover, .dropzone:focus-visible { border-color: var(--accent); }
.dropzone:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.dropzone.dragging { border-color: var(--accent); background: var(--accent-soft); }
.dropzone.disabled { opacity: 0.55; cursor: not-allowed; }
.dz-empty, .dz-file { display: flex; flex-direction: column; align-items: center; }
.dz-main { font-size: 16px; font-weight: 700; max-width: 100%; }
.dz-link { color: var(--accent); font-weight: 600; }
.dz-sub { margin-top: 8px; color: var(--text-muted); font-size: 12.5px; }

.progress-card { margin-top: 18px; }
.row { display: flex; justify-content: space-between; align-items: center; font-size: 14px; }
.status { font-weight: 600; }

.bar { margin-top: 12px; height: 8px; border-radius: 999px; background: var(--bg-elevated); overflow: hidden; }
.bar-fill { height: 100%; border-radius: 999px; background: var(--accent); transition: width .2s; }
.bar.running .bar-fill { width: 100%; }
.shimmer { background: linear-gradient(90deg, var(--accent-soft) 25%, var(--accent) 50%, var(--accent-soft) 75%); background-size: 200% 100%; animation: shimmer 1.4s infinite linear; }
@keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }

.actions { margin-top: 18px; display: flex; gap: 12px; }
.big { padding: 12px 26px; font-size: 15px; }
.batch-results { margin-top: 18px; }
.batch-row { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.batch-row .ellipsis { flex: 1 1 auto; min-width: 0; }
.upload-note { margin-top: 16px; display: flex; flex-wrap: wrap; gap: 6px 20px; font-size: 12.5px; color: var(--text-muted); }
.upload-note b { color: var(--text-secondary); font-weight: 600; }
.upload-privacy { margin-top: 8px; color: var(--text-muted); font-size: 11.5px; line-height: 1.6; max-width: 76ch; }

@media (max-width: 720px) {
  .dropzone { padding: 24px 16px; }
  .dz-main { font-size: 15px; }
  .actions { flex-wrap: wrap; }
  .actions .btn.big { flex: 1 1 100%; }
}
</style>
