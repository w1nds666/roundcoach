/**
 * 统一 API client（Phase 4A 架构基础）
 * - 统一解析后端错误 {detail: {code, message}} → ApiError（含中文 message）
 * - Bearer token 自动附加（localStorage 持久化）
 * - 401 自动清理登录态并广播 auth:expired（由 auth store 跳登录）
 * - uploadWithProgress：XHR 真实上传进度（fetch 无进度事件）
 */
const TOKEN_KEY = 'cs2_token'

export class ApiError extends Error {
  constructor(code, message, status, details = {}) {
    super(message)
    this.code = code
    this.status = status
    this.details = details
    if (details.existing_demo_id !== undefined) this.existing_demo_id = details.existing_demo_id
  }
}

export const tokenStore = {
  get() { return localStorage.getItem(TOKEN_KEY) || '' },
  set(t) { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) },
  clear() { localStorage.removeItem(TOKEN_KEY) },
}

export const authEvents = new EventTarget()

async function request(path, { method = 'GET', body, headers = {}, raw = false } = {}) {
  const h = { ...headers }
  const token = tokenStore.get()
  if (token) h.Authorization = `Bearer ${token}`
  if (body !== undefined && !(body instanceof FormData)) h['Content-Type'] = 'application/json'

  const resp = await fetch(path, {
    method,
    headers: h,
    body: body instanceof FormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
  })

  // 仅"带 token 的请求"返回 401 才是登录态失效；登录/注册接口的 401 是业务错误（如密码错误）
  if (resp.status === 401 && token) {
    tokenStore.clear()
    authEvents.dispatchEvent(new CustomEvent('auth:expired'))
    throw new ApiError('AUTH_REQUIRED', '登录已失效，请重新登录', 401)
  }

  const contentType = resp.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await resp.json() : await resp.text()

  if (!resp.ok) {
    const detail = data?.detail
    const code = typeof detail === 'object' ? detail.code : 'HTTP_ERROR'
    const message = typeof detail === 'object' ? detail.message : `请求失败（HTTP ${resp.status}）`
    throw new ApiError(code, message, resp.status, typeof detail === 'object' ? detail : {})
  }
  return raw ? { status: resp.status, data } : data
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: 'POST', body }),
  put: (path, body) => request(path, { method: 'PUT', body }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  delete: (path) => request(path, { method: 'DELETE' }),
  /** 6R15 长期教练画像：GET /api/coach/profile?window=10|30 */
  getCoachProfile: (window = 10) => request(`/api/coach/profile?window=${window}`),
  /** 带 Bearer 的 blob 获取（P1-2：受保护图片的 authenticated fetch，供 objectURL 展示） */
  async getBlob(path) {
    const token = tokenStore.get()
    const resp = await fetch(path, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (resp.status === 401) {
      tokenStore.clear()
      authEvents.dispatchEvent(new CustomEvent('auth:expired'))
      throw new ApiError('AUTH_REQUIRED', '登录已失效，请重新登录', 401)
    }
    if (!resp.ok) {
      let code = 'HTTP_ERROR'
      let message = `请求失败（HTTP ${resp.status}）`
      try {
        const data = await resp.json()
        const detail = data?.detail
        if (typeof detail === 'object') { code = detail.code; message = detail.message || message }
      } catch { /* 非 JSON */ }
      throw new ApiError(code, message, resp.status)
    }
    return resp.blob()
  },
  /** multipart 上传（XHR，真实进度） */
  upload(path, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', path)
      const token = tokenStore.get()
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      }
      xhr.onload = () => {
        let data = null
        try { data = JSON.parse(xhr.responseText) } catch { /* 非 JSON */ }
        if (xhr.status === 401 && token) {
          tokenStore.clear()
          authEvents.dispatchEvent(new CustomEvent('auth:expired'))
          reject(new ApiError('AUTH_REQUIRED', '登录已失效，请重新登录', 401))
          return
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data)
          return
        }
        const detail = data?.detail
        const code = typeof detail === 'object' ? detail.code : 'HTTP_ERROR'
        const message = typeof detail === 'object' ? detail.message : `上传失败（HTTP ${xhr.status}）`
        reject(new ApiError(code, message, xhr.status, typeof detail === 'object' ? detail : {}))
      }
      xhr.onerror = () => reject(new ApiError('NETWORK_ERROR', '网络异常，请检查连接后重试', 0))
      xhr.ontimeout = () => reject(new ApiError('TIMEOUT', '上传超时，请重试', 0))

      const form = new FormData()
      form.append('file', file)
      xhr.send(form)
    })
  },
  async uploadBatch(path, files) {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    return request(path, { method: 'POST', body: form })
  },
}
