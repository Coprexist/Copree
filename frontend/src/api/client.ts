/**
 * API 客户端
 * 封装 fetch，自动添加 JWT 认证头
 * 桌面端从 localStorage 读取实例地址拼 API 路径，Web 端保持 '/api' 相对路径
 */
import { type Result, success, failure } from '../utils/result'
import { saveBlob } from '../utils/download'

/** 嵌入模式检测（与 embed/bridge 的 isEmbedded 同规则；此处独立实现避免循环依赖） */
function isEmbeddedMode(): boolean {
  try {
    return typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('embed')
  } catch {
    return false
  }
}

/** 嵌入模式下的 API 前缀（宿主 DSH 通过同源代理提供） */
const EMBED_API_PREFIX = '/copree-api'

function getApiBaseUrl(): string {
  // 嵌入模式（DSH iframe）：走宿主同源代理前缀
  if (isEmbeddedMode()) return EMBED_API_PREFIX
  // 桌面端：从 localStorage 读取实例地址
  const stored = localStorage.getItem('instance_url')
  if (stored) {
    return stored.replace(/\/+$/, '') + '/api'
  }
  // Web 端：使用默认值
  return '/api'
}

/**
 * 附件下载地址。<img>/<a> 带不上 Authorization 头，token 只能走 query；
 * 嵌入模式与桌面端的 base 差异也在这里收敛，组件里不再手拼 /api。
 */
function fileDownloadUrl(fileId: number): string {
  return `${getApiBaseUrl()}/fs/download/${fileId}?token=${localStorage.getItem('access_token') || ''}`
}

export { getApiBaseUrl, fileDownloadUrl }

/** 401 统一处理：嵌入模式通知宿主（不整页跳转，避免 iframe 跳出宿主）；独立模式清 token 跳登录页 */
function handleUnauthorized(path: string) {
  if (path.endsWith('/auth/login') || path.endsWith('/auth/register')) return
  localStorage.removeItem('access_token')
  if (isEmbeddedMode()) {
    try {
      window.parent?.postMessage({ source: 'copree-embed', type: 'unauthorized' }, '*')
    } catch {
      /* 宿主不可达时静默 */
    }
    return
  }
  window.location.href = '/login'
}

/**
 * 后端 detail 有三种形状：一句话、字段校验错误数组（FastAPI 422 过滤后的
 * `{loc, msg, type}`）、以及少数自定义对象。错误文本最终会被塞进 JSX，
 * 直接把数组/对象交出去会让 React 抛 "Objects are not valid as a React child"、
 * 整页白屏——**这类归一化只允许在这里做**，调用方拿到的永远是字符串。
 */
export function errorText(detail: unknown, fallback = 'Request failed'): string {
  const list = Array.isArray(detail) ? detail : [detail]
  const parts = list.map(oneErrorPart).filter(Boolean)
  return parts.length ? parts.join('；') : fallback
}

function oneErrorPart(item: unknown): string {
  if (item === null || item === undefined) return ''
  if (typeof item === 'string') return item.trim()
  if (typeof item === 'number' || typeof item === 'boolean') return String(item)
  if (typeof item !== 'object') return ''
  const row = item as Record<string, unknown>
  // 422 的字段校验行：loc 末段就是字段名（首段固定是 body/query/path，对人没信息）
  const text = [row.msg, row.message, row.detail, row.error].find((v): v is string => typeof v === 'string' && v.trim() !== '')
  if (text) {
    const field = Array.isArray(row.loc)
      ? row.loc.filter((v) => typeof v === 'string' && v !== 'body').pop()
      : undefined
    return field ? `${field}: ${text}` : text
  }
  try { return JSON.stringify(item) } catch { return '' }
}

class ApiError extends Error {
  status: number
  detail: string
  constructor(message: string, status: number) {
    super(message)
    this.status = status
    this.detail = message
  }
}

async function request<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = localStorage.getItem('access_token')

  const body = options.body
  const isFormData = body instanceof FormData

  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  }
  // FormData 让浏览器自动设置 Content-Type（含 boundary），不要手动盖
  if (!isFormData) {
    headers['Content-Type'] = 'application/json'
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    ...options,
    headers,
  })

  // 401 且不是登录请求 → token 过期/无效（嵌入模式通知宿主，独立模式跳登录页）
  if (res.status === 401 && !path.endsWith('/auth/login') && !path.endsWith('/auth/register')) {
    handleUnauthorized(path)
    throw new ApiError('Unauthorized', 401)
  }

  // 软维护提示（API 正常但带维护头）
  if (res.headers.get('X-Maintenance') === 'true') {
    localStorage.setItem('_maint_detected', '1')
    window.dispatchEvent(new CustomEvent('maintenance-soft'))
  }

  if (res.status === 503) {
    try { const body = JSON.parse(await res.clone().text()); if (body.maintenance) {
      localStorage.setItem('_maint_detected', '1')
      window.dispatchEvent(new CustomEvent('maintenance-mode', { detail: body }))
      throw new ApiError(errorText(body.detail, '服务器维护中'), 503)
    } } catch {}
  }

  // 硬维护弹窗清除：仅当之前显示过硬维护弹窗、且本次响应确认非维护时才关闭。
  // 注意：/admin/*、/auth/* 等 bypass 路径永远无维护头，不能据此清除软维护提示；
  // 软维护的关闭统一由 Layout 轮询 /maintenance-msg 权威判定。
  if (res.status !== 503 && res.headers.get('X-Maintenance') !== 'true' && localStorage.getItem('_maint_hard_visible')) {
    localStorage.removeItem('_maint_hard_visible')
    localStorage.removeItem('_maint_detected')
    window.dispatchEvent(new CustomEvent('maintenance-cleared'))
  }

  // 安全解析 JSON：处理空 body / 非 JSON 响应
  let data: any
  try {
    const text = await res.text()
    data = text ? JSON.parse(text) : {}
  } catch {
    if (!res.ok) {
      throw new ApiError(`Request failed (${res.status})`, res.status)
    }
    // 200 但 body 不是 JSON：最常见的来由是请求被静态兜底接管了（拿回 index.html）。
    // 以前这里**静默**返回 {}，调用方拿到的"数组"变成对象，报错落在离现场很远的地方
    //（`messages.map is not a function`——2026-10-27 桌面版点开对话就崩，根因就是这条静默）。
    // 空 body 仍然返回 {}：那是正常的 204/无内容语义，不走这里。
    throw new ApiError(
      `服务返回了非 JSON 响应（${res.status}，${res.headers.get('content-type') || '无 content-type'}）：`
      + '接口可能被静态页面兜底接管了，检查反向代理或 /api 前缀。',
      res.status,
    )
  }

  if (!res.ok) {
    throw new ApiError(errorText(data.detail, `Request failed (${res.status})`), res.status)
  }

  return data
}

/** 上传文件（multipart/form-data），返回 {file_id, name, path, size, mime_type} */
async function uploadFile(path: string, file: File): Promise<any> {
  const token = localStorage.getItem('access_token')
  const formData = new FormData()
  formData.append('file', file)

  const headers: Record<string, string> = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    method: 'POST',
    headers,
    body: formData,
  })

  // 401 且不是登录请求 → token 过期/无效（嵌入模式通知宿主，独立模式跳登录页）
  if (res.status === 401 && !path.endsWith('/auth/login') && !path.endsWith('/auth/register')) {
    handleUnauthorized(path)
    throw new ApiError('Unauthorized', 401)
  }

  // 友好提示：413 文件过大（代理/后端任意层拦截）
  if (res.status === 413) {
    throw new ApiError('文件过大，请检查文件大小限制', 413)
  }

  // 安全解析 JSON：处理空 body / 非 JSON 响应
  let data: any
  try {
    const text = await res.text()
    data = text ? JSON.parse(text) : {}
  } catch {
    if (!res.ok) {
      throw new ApiError(`上传失败 (${res.status})`, res.status)
    }
    return {}
  }

  if (!res.ok) {
    throw new ApiError(errorText(data.detail, `上传失败 (${res.status})`), res.status)
  }
  return data
}

/** Result 风格的请求封装（不抛异常，返回 Result<T, ApiError>） */
async function safeRequest<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<Result<T, ApiError>> {
  try {
    const data = await request<T>(path, options)
    return success(data)
  } catch (e) {
    if (e instanceof ApiError) return failure(e)
    return failure(new ApiError(String(e), 0))
  }
}

const jsonBody = (body?: any) => body instanceof FormData ? body : JSON.stringify(body)

/** 从 Content-Disposition 取文件名（RFC 5987 优先，中文名也能还原） */
function filenameFromDisposition(header: string | null): string {
  if (!header) return ''
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (star) { try { return decodeURIComponent(star[1].trim()) } catch { return star[1].trim() } }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain ? plain[1].trim() : ''
}

/** 带鉴权下载文件（**全站"另存为"唯一入口**：会话/群聊/私信导出、备份、世界包、docx 转换…）
 *
 *  复用同一套 base + token（嵌入模式走宿主代理 /copree-base，桌面端走实例地址——
 *  以前各处自己写 `/api/...`，嵌进 DSH 面板就 404）；
 *  文件名以服务端 Content-Disposition 为准（RFC 5987，中文名也正确），拿不到才用兜底名；
 *  init 支持 POST（如 docx：先 POST 拿二进制）。落盘交给 utils/download.saveBlob。 */
async function downloadFile(path: string, fallbackName: string,
                            init: { method?: string; body?: any } = {}): Promise<void> {
  const token = localStorage.getItem('access_token')
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (init.body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    method: init.method || 'GET',
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  })
  if (res.status === 401) { handleUnauthorized(path); throw new ApiError('Unauthorized', 401) }
  if (!res.ok) {
    let detail: unknown
    try { detail = (await res.json())?.detail } catch { /* 非 JSON 就用状态码 */ }
    throw new ApiError(errorText(detail, `HTTP ${res.status}`), res.status)
  }
  saveBlob(await res.blob(), filenameFromDisposition(res.headers.get('Content-Disposition')) || fallbackName)
}

export const api = {
  get: <T = any>(path: string) => request<T>(path),
  post: <T = any>(path: string, body?: any) =>
    request<T>(path, { method: 'POST', body: jsonBody(body) }),
  put: <T = any>(path: string, body?: any) =>
    request<T>(path, { method: 'PUT', body: jsonBody(body) }),
  patch: <T = any>(path: string, body?: any) =>
    request<T>(path, { method: 'PATCH', body: jsonBody(body) }),
  delete: <T = any>(path: string) =>
    request<T>(path, { method: 'DELETE' }),
  upload: uploadFile,
  download: downloadFile,
  // Result 风格 API（不抛异常）
  safe: {
    get: <T = any>(path: string) => safeRequest<T>(path),
    post: <T = any>(path: string, body?: any) =>
      safeRequest<T>(path, { method: 'POST', body: jsonBody(body) }),
    put: <T = any>(path: string, body?: any) =>
      safeRequest<T>(path, { method: 'PUT', body: jsonBody(body) }),
    patch: <T = any>(path: string, body?: any) =>
      safeRequest<T>(path, { method: 'PATCH', body: jsonBody(body) }),
    delete: <T = any>(path: string) =>
      safeRequest<T>(path, { method: 'DELETE' }),
  },
}

export { ApiError }
