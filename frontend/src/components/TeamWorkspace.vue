<script setup>
/**
 * 6R14：Max 团队工作区（Profile 内）。
 * - GET /api/team 展示团队与成员（掩码 id / role / slot_type / status）。
 * - 未创建且有效 Max → 可「创建团队工作区」。
 * - owner/admin 可添加成员（user_id + main/sub）与移除成员。
 * - 成员以已验证 Steam 身份占座（不手填 SteamID）；错误用 ApiError.message 展示。
 */
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '../api/client'
import { authStore } from '../stores/auth'
import { isMax } from '../utils/subscription'

const team = ref(null)
const members = ref([])
const loading = ref(true)
const loadError = ref('')

const createOpen = ref(false)
const teamName = ref('')
const creating = ref(false)
const createError = ref('')

const addUserId = ref('')
const addSlot = ref('main')
const adding = ref(false)
const addError = ref('')
const removingId = ref(null)

const currentUser = computed(() => authStore.state.user)
const max = computed(() => isMax(currentUser.value))
const currentMember = computed(() => members.value.find((m) => m.user_id === currentUser.value?.id) || null)
const canManage = computed(() => !!currentMember.value && ['owner', 'admin'].includes(currentMember.value.role))

const ROLE_CN = { owner: '拥有者', admin: '管理员', member: '成员' }
const SLOT_CN = { main: '主力', sub: '替补' }
const STATUS_CN = { active: '正常', removed: '已移除' }

const usedMain = computed(() => members.value.filter((m) => m.slot_type === 'main').length)
const usedSub = computed(() => members.value.filter((m) => m.slot_type === 'sub').length)

async function loadTeam() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await api.get('/api/team')
    team.value = data.team
    members.value = data.members || []
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : '团队信息加载失败，请刷新重试'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createOpen.value = true
  teamName.value = ''
  createError.value = ''
}
function closeCreate() { createOpen.value = false }

async function confirmCreate() {
  if (creating.value) return
  const name = teamName.value.trim()
  if (!name) { createError.value = '请输入团队名称'; return }
  creating.value = true
  createError.value = ''
  try {
    const data = await api.post('/api/team', { name })
    team.value = data.team
    members.value = data.members || []
    createOpen.value = false
  } catch (e) {
    createError.value = e instanceof ApiError ? e.message : '创建团队失败，请稍后重试'
  } finally {
    creating.value = false
  }
}

async function addMember() {
  if (adding.value) return
  const uid = Number(addUserId.value.trim())
  if (!addUserId.value.trim() || !Number.isInteger(uid) || uid <= 0) { addError.value = '请输入有效的用户 ID'; return }
  adding.value = true
  addError.value = ''
  try {
    await api.post('/api/team/members', { user_id: uid, slot_type: addSlot.value })
    addUserId.value = ''
    await loadTeam()
  } catch (e) {
    addError.value = e instanceof ApiError ? e.message : '添加成员失败，请稍后重试'
  } finally {
    adding.value = false
  }
}

async function removeMember(userId) {
  if (removingId.value != null) return
  removingId.value = userId
  addError.value = ''
  try {
    await api.delete(`/api/team/members/${userId}`)
    await loadTeam()
  } catch (e) {
    addError.value = e instanceof ApiError ? e.message : '移除成员失败，请稍后重试'
  } finally {
    removingId.value = null
  }
}

onMounted(loadTeam)
</script>

<template>
  <div class="team card">
    <div class="head">
      <h2 class="sub-h">团队工作区</h2>
      <span v-if="team" class="pill pill-info">高阶版</span>
      <span v-else-if="!loading && !loadError && max" class="pill pill-muted">高阶版</span>
    </div>

    <div v-if="loading" class="state">加载团队信息…</div>
    <p v-else-if="loadError" class="form-error-box">{{ loadError }}</p>

    <!-- 未创建 & 有效 Max：创建入口 -->
    <div v-else-if="!team">
      <template v-if="max">
        <p class="desc">把已验证 Steam 的队友邀请进团队工作区：5 名主力 + 最多 2 名替补，用于团队复盘与团队深度报告。成员以绑定 Steam 身份占座，不能手填 SteamID。</p>
        <button class="btn btn-primary" @click="openCreate">创建团队工作区</button>
      </template>
      <div v-else class="locked-box">
        <div class="lock-icon">🔒</div>
        <p class="lock-title">团队工作区属于高阶版（Max）功能</p>
        <p class="lock-desc">升级为 Max 后可创建团队工作区，把队友的 Steam 身份汇聚为团队 roster，解锁团队复盘与团队深度报告。</p>
      </div>
    </div>

    <!-- 已创建：成员列表 + 管理 -->
    <template v-else>
      <div class="team-brief">
        <p class="team-name ellipsis">{{ team.name }}</p>
        <span class="slot-note num">{{ usedMain }}/5 主力 · {{ usedSub }}/2 替补</span>
      </div>

      <div class="members">
        <div v-for="m in members" :key="m.user_id" class="member">
          <div class="m-info">
            <span class="m-steam num ellipsis">{{ m.steam_id64_masked }}</span>
            <span class="m-tags">
              <span class="pill" :class="m.role === 'owner' || m.role === 'admin' ? 'pill-info' : 'pill-muted'">{{ ROLE_CN[m.role] || m.role }}</span>
              <span class="pill" :class="m.slot_type === 'sub' ? 'pill-warn' : 'pill-ok'">{{ SLOT_CN[m.slot_type] || m.slot_type }}</span>
              <span class="pill pill-muted">{{ STATUS_CN[m.status] || m.status }}</span>
              <span v-if="m.user_id === currentUser?.id" class="pill pill-ok">我</span>
            </span>
          </div>
          <button v-if="canManage && m.user_id !== currentUser?.id" class="btn btn-ghost rm" :disabled="removingId != null" @click="removeMember(m.user_id)">
            {{ removingId === m.user_id ? '移除中…' : '移除' }}
          </button>
        </div>
      </div>

      <!-- 添加成员（owner/admin） -->
      <div v-if="canManage" class="add">
        <div class="add-row">
          <input v-model="addUserId" type="number" min="1" class="input add-id" placeholder="用户 ID" />
          <select v-model="addSlot" class="input add-slot"><option value="main">主力</option><option value="sub">替补</option></select>
          <button class="btn btn-secondary" :disabled="adding" @click="addMember">{{ adding ? '添加中…' : '添加成员' }}</button>
        </div>
        <p v-if="addError" class="form-error-box">{{ addError }}</p>
      </div>

      <p class="note">成员须先完成 Steam 验证才能占座（以绑定身份为准，不能手填 SteamID）。roster 变更有 7 天冷却，移除成员会暂停其占位。</p>
    </template>

    <!-- 创建弹窗 -->
    <div v-if="createOpen" class="backdrop" @click.self="closeCreate">
      <div class="modal card" role="dialog" aria-modal="true" aria-label="创建团队工作区">
        <h3>创建团队工作区</h3>
        <p class="modal-desc">创建后你将作为拥有者（主力）占座，并可邀请已验证 Steam 的队友。一个 Max 账号最多创建一个团队工作区。</p>
        <label class="field">
          <span>团队名称</span>
          <input v-model="teamName" class="input" maxlength="64" placeholder="例如：深渊眼镜蛇" />
        </label>
        <p v-if="createError" class="form-error-box">{{ createError }}</p>
        <div class="actions">
          <button class="btn btn-primary" :disabled="creating" @click="confirmCreate">{{ creating ? '创建中…' : '创建' }}</button>
          <button class="btn" :disabled="creating" @click="closeCreate">取消</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.team { display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: center; gap: 10px; }
.sub-h { margin: 0; }
.state { color: var(--text-muted); font-size: 13px; padding: 6px 0; }
.desc { color: var(--text-secondary); font-size: 13px; line-height: 1.7; max-width: 76ch; }
.note { color: var(--text-muted); font-size: 12px; line-height: 1.6; }
.btn { align-self: flex-start; min-height: 44px; padding: 9px 18px; }

.team-brief { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; min-width: 0; }
.team-name { font-size: 16px; font-weight: 700; color: var(--text-primary); margin: 0; min-width: 0; }
.slot-note { color: var(--text-muted); font-size: 12.5px; white-space: nowrap; }

.members { display: flex; flex-direction: column; gap: 8px; }
.member { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg-elevated); }
.m-info { display: flex; flex-direction: column; gap: 6px; min-width: 0; flex: 1; }
.m-steam { color: var(--text-primary); font-size: 13px; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.m-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.rm { min-height: 40px; padding: 6px 10px; font-size: 12.5px; flex-shrink: 0; }

.add { display: flex; flex-direction: column; gap: 8px; padding-top: 4px; border-top: 1px solid var(--border); }
.add-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.add-id { flex: 1 1 120px; min-width: 0; }
.add-slot { flex: 0 0 90px; }
.add-row .btn { min-height: 44px; align-self: center; }

.locked-box { padding: 18px 16px; display: flex; flex-direction: column; gap: 8px; background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius); }
.lock-icon { font-size: 20px; }
.lock-title { font-weight: 700; font-size: 15px; }
.lock-desc { color: var(--text-secondary); font-size: 13px; line-height: 1.7; }

.backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, .55); display: grid; place-items: center; z-index: 20; padding: 16px; }
.modal { width: min(460px, 100%); display: flex; flex-direction: column; gap: 12px; }
.modal h3 { margin: 0; font-size: 18px; }
.modal-desc { color: var(--text-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.field { display: flex; flex-direction: column; gap: 5px; color: var(--text-secondary); font-size: 13px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; }
.actions .btn { min-height: 44px; }

@media (max-width: 720px) {
  .member { flex-direction: column; align-items: flex-start; }
  .rm { align-self: flex-end; }
}
</style>
