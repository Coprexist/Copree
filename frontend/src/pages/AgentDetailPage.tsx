import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { STATE_BADGE_COLORS } from '../constants'
import Toggle from '../components/Toggle'
import { useT } from '../i18n/I18nContext'
import { getStatusTextStyle, STATUS_COLORS } from '../utils/statusColor.tsx'
import {
  ArrowLeft, Trash2, Download, Upload, Key, Edit3,
  Image, HardDrive, Brain, Copy, Check, X, RefreshCw, Bot, Settings, User,
  ScrollText, Loader2, ChevronRight, ChevronDown, Bookmark, Tag, Database,
} from 'lucide-react'
import AvatarPickerModal from '../components/AvatarPickerModal'
import AgentSettingsModal from '../components/AgentSettingsModal'
import { Dialog, EmptyState } from '../components/ui'
import FilePreviewModal from '../components/FilePreviewModal'
import QQChannelCard from '../components/channels/QQChannelCard'

/** 扩展名→MIME 类型映射（后端未返回 mime_type 时 fallback） */
const EXT_MIME_MAP: Record<string, string> = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
  webp: 'image/webp', svg: 'image/svg+xml', bmp: 'image/bmp',
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  json: 'application/json', xml: 'application/xml', yaml: 'application/x-yaml', yml: 'application/x-yaml',
  js: 'application/javascript', ts: 'text/typescript', py: 'text/x-python',
  sh: 'application/x-shellscript', bash: 'application/x-shellscript',
  md: 'text/markdown', txt: 'text/plain', html: 'text/html', css: 'text/css',
  csv: 'text/csv', log: 'text/plain',
  mp4: 'video/mp4', webm: 'video/webm', mp3: 'audio/mpeg', wav: 'audio/wav',
  zip: 'application/zip', tar: 'application/x-tar', gz: 'application/gzip',
}

/** 获取文件 MIME 类型：优先后端返回值，缺失时从文件名推断 */
function getMimeType(file: { name: string; mime_type?: string }): string {
  if (file.mime_type) return file.mime_type
  const ext = file.name.split('.').pop()?.toLowerCase() || ''
  return EXT_MIME_MAP[ext] || ''
}

interface Agent {
  id: number
  owner_id: number
  name: string
  state: string
  original_system_prompt: string | null
  original_temperature: number
  original_top_p: number
  original_presence_penalty: number
  original_frequency_penalty: number
  current_system_prompt: string | null
  current_temperature: number
  current_top_p: number
  current_presence_penalty: number
  current_frequency_penalty: number
  chat_model: string | null
  work_model: string | null
  thinking_enabled: boolean
  hide_ai_identity: boolean
  ai_type: string
  config_profile: string
  delay_reply_enabled: boolean | null
  max_tool_rounds: number
  alarm_max_tool_rounds: number
  force_alarm_on_end: boolean
  max_alarms: number
  allow_friend_requests: boolean
  auto_respond_friend_request: boolean
  api_credit_cost: number
  api_base_url: string | null
  has_api_key: boolean
  avatar_url: string | null
  bio: string | null
  status_text: string | null
  status_color: string | null
  api_token: string | null
  is_ai_editable: boolean
  reminder_grace: string
  discoverable: boolean
  memory_load_mode: string
  memory_recent_count: number
  memory_shared_scope: string
  created_at: string
}

interface StorageInfo {
  total_size: number
  file_count: number
  quota_bytes: number
  quota_mb: number
  usage_percent: number
  files: Array<{ id: number; name: string; size: number; path: string; mime_type?: string }>
}

interface FileReference {
  referrer_type: string
  referrer_id: number
  ref_type: string
  display: string
}

interface LogSummary {
  id: number
  agent_id: number
  conversation_type: string
  message_count: number
  token_usage: any
  has_output: boolean
  model: string | null
  thinking_enabled: boolean
  preview: any[]
  created_at: string | null
}

interface MemoryItem {
  id: number
  title: string
  content: string | null
  scope: string
  group_id: number | null
  created_at: string | null
}

interface WorkspaceFiles {
  todo: string
  plan: string
  journal: string
}

// ── 向量记忆卡片（可折叠内容 + scope 彩色标签） ──

function VectorMemoryCard({ mem, t: _t }: { mem: MemoryItem; t: any }) {
  const [showFull, setShowFull] = useState(false)
  const scopeColor: Record<string, string> = {
    private: 'bg-primary-500/10 text-primary-400 border-primary-500/20',
    group: 'bg-mint-500/10 text-mint-400 border-mint-500/20',
    cross_user: 'bg-sky-500/10 text-sky-400 border-sky-500/20',
  }
  const scopeLabel: Record<string, string> = {
    private: '私有', group: '群组', cross_user: '跨用户',
  }
  return (
    <div className="p-3 rounded-control bg-canvas border border-border">
      <button onClick={() => setShowFull(!showFull)} className="w-full text-left">
        <div className="flex items-center justify-between mb-0.5">
          <span className="text-sm font-medium text-textPrimary">{mem.title}</span>
          <span className={`text-3xs px-1.5 py-0.5 rounded border ${scopeColor[mem.scope] || scopeColor.private}`}>
            {scopeLabel[mem.scope] || mem.scope}
          </span>
        </div>
      </button>
      {showFull && mem.content && (
        <p className="text-xs text-textSecondary mt-2 pt-2 border-t border-border/50 leading-relaxed whitespace-pre-wrap">
          {mem.content}
        </p>
      )}
      <p className="text-3xs text-textMuted mt-1">
        {mem.created_at ? new Date(mem.created_at).toLocaleString('zh-CN') : ''}
      </p>
    </div>
  )
}

// ── 结构化记忆视图 ──

interface StructuredCategory {
  category: string
  record_count: number
  sub_count: number
  subs: StructuredSub[]
}

interface StructuredSub {
  sub_key: string
  field_count: number
  last_update: string | null
  fields: Record<string, string>
}

function StructuredMemoryView({ agentId }: { agentId: number }) {
  const t = useT()
  const [data, setData] = useState<StructuredCategory[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedCat, setExpandedCat] = useState<string | null>(null)
  const [expandedSub, setExpandedSub] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.get<{ categories: StructuredCategory[] }>(`/agents/${agentId}/structured-records`)
      .then(res => { if (!cancelled) setData(res.categories) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [agentId])

  if (loading) return <div className="flex justify-center py-8"><Loader2 size={18} className="animate-spin text-textMuted" /></div>

  if (!data || data.length === 0) return (
    <EmptyState icon={Database} title="暂无结构化记忆" description="AI 还没有记录任何结构化信息，随着对话进行会自动生成" />
  )

  // 为不同类别分配不同图标
  const CAT_ICONS: Record<string, React.ElementType> = {
    user_profile: User, user: User,
    preferences: Bookmark, preference: Bookmark,
    knowledge: Brain, facts: Brain,
    tags: Tag, labels: Tag,
  }

  return (
    <div className="space-y-1.5 max-h-[500px] overflow-y-auto">
      {data.map((cat) => {
        const isCatOpen = expandedCat === cat.category
        const CatIcon = CAT_ICONS[cat.category] || Bookmark
        return (
          <div key={cat.category} className="rounded-control border border-border overflow-hidden">
            <button
              onClick={() => setExpandedCat(isCatOpen ? null : cat.category)}
              className="w-full flex items-center gap-2.5 px-3.5 py-2.5 hover:bg-elevated transition-colors text-left"
            >
              <CatIcon size={15} className="text-primary-400 shrink-0" />
              <span className="text-sm font-medium text-textPrimary">{cat.category}</span>
              <span className="text-2xs text-textMuted bg-canvas px-1.5 py-0.5 rounded ml-auto">{cat.record_count} 条</span>
              {isCatOpen ? <ChevronDown size={14} className="text-textMuted shrink-0" /> : <ChevronRight size={14} className="text-textMuted shrink-0" />}
            </button>
            {isCatOpen && (
              <div className="border-t border-border bg-canvas/40">
                {cat.subs.map((sub) => {
                  const isSubOpen = expandedSub === `${cat.category}/${sub.sub_key}`
                  return (
                    <div key={sub.sub_key}>
                      <button
                        onClick={() => setExpandedSub(isSubOpen ? null : `${cat.category}/${sub.sub_key}`)}
                        className="w-full flex items-center gap-2 px-7 py-1.5 hover:bg-elevated transition-colors text-left"
                      >
                        {isSubOpen ? <ChevronDown size={11} className="text-textMuted shrink-0" /> : <ChevronRight size={11} className="text-textMuted shrink-0" />}
                        <span className="text-xs text-textPrimary font-medium">{sub.sub_key}</span>
                        <span className="text-3xs text-textMuted ml-auto">{sub.field_count} 项</span>
                        {sub.last_update && (
                          <span className="text-3xs text-textMuted/60">{new Date(sub.last_update).toLocaleDateString('zh-CN')}</span>
                        )}
                      </button>
                      {isSubOpen && (
                        <div className="border-t border-border/40 ml-9 py-1.5 px-3 space-y-1">
                          {Object.entries(sub.fields).map(([field, value]) => (
                            <div key={field} className="flex gap-2 text-xs py-0.5">
                              <span className="text-primary-400 font-medium shrink-0 min-w-[70px] text-2xs">{field}</span>
                              <span className="text-textSecondary/90 break-words leading-relaxed">{value.length > 120 ? value.slice(0, 120) + '…' : value}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default function AgentDetailPage() {
  const t = useT()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user, refreshUser } = useAuth()
  const agentId = parseInt(id || '0')

  const [agent, setAgent] = useState<Agent | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<'info' | 'channels' | 'memories' | 'storage' | 'workspace' | 'logs' | 'collaborators'>('info')
  // 页签文案：一张表代替七层三元表达式；每项都写成字面量 t('...')，
  // i18n 静态检查（扫 t('字面量')）才看得见，加页签只动一行
  const tabLabels: Record<string, string> = {
    info: t('agentDetail.tabInfo'),
    channels: t('agentDetail.tabChannels'),
    memories: t('agentDetail.tabMemories'),
    storage: t('agentDetail.tabStorage'),
    workspace: t('agentDetail.tabWorkspace'),
    logs: t('agentDetail.tabLogs'),
    collaborators: t('agentDetail.tabCollaborators'),
  }
  const [showFullSettings, setShowFullSettings] = useState(false)
  const [modelOptions, setModelOptions] = useState<{ value: string; label: string; provider_name?: string; provider_key?: string }[]>([])
  const [providersList, setProvidersList] = useState<{ name: string; provider: string; base_url: string; thinking_supported: boolean; is_default: boolean; models: { value: string; label: string }[] }[]>([])
  const [defaultModels, setDefaultModels] = useState({ chat_model: '', work_model: '' })
  const [thinkingSupported, setThinkingSupported] = useState(true)
  // 文件删除确认
  const [deleteFileTarget, setDeleteFileTarget] = useState<{ id: number; name: string } | null>(null)
  const [deleteFileRefs, setDeleteFileRefs] = useState<{ reference_count: number; references: FileReference[] } | null>(null)
  const [deleteFileLoading, setDeleteFileLoading] = useState(false)
  const [deletingFile, setDeletingFile] = useState(false)

  // Delete
  const [showDelete, setShowDelete] = useState(false)
  const [deleteConfirmName, setDeleteConfirmName] = useState('')
  const [deleting, setDeleting] = useState(false)

  // Prompt edit
  const [editingPrompt, setEditingPrompt] = useState(false)
  const [promptText, setPromptText] = useState('')
  const [savingPrompt, setSavingPrompt] = useState(false)

  // Token
  const [token, setToken] = useState<string | null>(null)
  const [tokenMasked, setTokenMasked] = useState<string | null>(null)
  const [generatingToken, setGeneratingToken] = useState(false)
  const [showToken, setShowToken] = useState(false)

  // Profile 颜色选择器 — 用本地状态避免频繁 API 调用
  const [profileStatusColor, setProfileStatusColor] = useState('')

  // Export / Import
  const [copied, setCopied] = useState(false)
  const [importing, setImporting] = useState(false)

  // Avatar
  const [avatarPickerOpen, setAvatarPickerOpen] = useState(false)
  const [uploadingAvatar, setUploadingAvatar] = useState(false)
  const [previewFile, setPreviewFile] = useState<{ id: number; name: string; size: number; mime: string } | null>(null)

  // Storage
  const [storage, setStorage] = useState<StorageInfo | null>(null)

  // Logs
  const [logs, setLogs] = useState<LogSummary[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [logsError, setLogsError] = useState(false)
  const [selectedLog, setSelectedLog] = useState<any>(null)
  const [logDetailLoading, setLogDetailLoading] = useState(false)
  const [logExporting, setLogExporting] = useState(false)

  // Memories
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [memPage, setMemPage] = useState(1)
  const [memTotal, setMemTotal] = useState(0)
  const [memSubTab, setMemSubTab] = useState<'structured' | 'vector'>('structured')

  // Workspace
  const [workspace, setWorkspace] = useState<WorkspaceFiles>({ todo: '', plan: '', journal: '' })
  const [wsActive, setWsActive] = useState<'todo' | 'plan' | 'journal'>('todo')

  // Collaborators
  interface Collaborator {
    id: number; agent_id: number; user_id: number; username?: string; avatar_url?: string | null
    can_edit: boolean; can_delete: boolean; can_manage_collaborators: boolean
    created_at: string | null
  }
  const [collaborators, setCollaborators] = useState<Collaborator[]>([])
  const [isOwner, setIsOwner] = useState(false)
  const [collabSearch, setCollabSearch] = useState('')
  const [collabSearchResults, setCollabSearchResults] = useState<Array<{id:number;username:string;avatar_url:string|null}>>([])
  const [addingCollab, setAddingCollab] = useState(false)
  const [newCollabPerms, setNewCollabPerms] = useState({ can_edit: true, can_delete: false, can_manage_collaborators: false })

  const loadWorkspace = useCallback(async () => {
    try {
      const data = await api.get<WorkspaceFiles>(`/agents/${agentId}/workspace`)
      setWorkspace(data)
    } catch { /* ignore */ }
  }, [agentId])

  const loadCollaborators = useCallback(async () => {
    try {
      const data = await api.get<{ collaborators: Collaborator[]; is_owner: boolean }>(`/agents/${agentId}/collaborators`)
      setCollaborators(data.collaborators || [])
      setIsOwner(data.is_owner)
    } catch { /* ignore */ }
  }, [agentId])

  const searchUsers = useCallback(async (q: string) => {
    if (q.length < 1) { setCollabSearchResults([]); return }
    try {
      const data = await api.get<{ users: Array<{id:number;username:string;avatar_url:string|null}> }>(`/user/search?q=${encodeURIComponent(q)}`)
      setCollabSearchResults(data.users || [])
    } catch { setCollabSearchResults([]) }
  }, [])

  const handleAddCollaborator = async (userId: number) => {
    setAddingCollab(true)
    try {
      await api.post(`/agents/${agentId}/collaborators`, { user_id: userId, ...newCollabPerms })
      setCollabSearch(''); setCollabSearchResults([])
      setNewCollabPerms({ can_edit: true, can_delete: false, can_manage_collaborators: false })
      await loadCollaborators()
    } catch (e: any) { alert(e?.detail || e?.message || '添加失败') }
    finally { setAddingCollab(false) }
  }

  const handleRemoveCollaborator = async (userId: number) => {
    if (!confirm('确认移除该合作者？')) return
    try {
      await api.delete(`/agents/${agentId}/collaborators/${userId}`)
      await loadCollaborators()
    } catch (e: any) { alert(e?.detail || e?.message || '移除失败') }
  }

  const handleUpdateCollaborator = async (userId: number, perms: Partial<typeof newCollabPerms>) => {
    try {
      await api.put(`/agents/${agentId}/collaborators/${userId}`, perms)
      await loadCollaborators()
    } catch (e: any) { alert(e?.detail || e?.message || '更新失败') }
  }

  const loadAgent = useCallback(async () => {
    try {
      const data = await api.get(`/agents/${agentId}`)
      setAgent(data)
      setPromptText(data.current_system_prompt || '')
    } catch {
      navigate('/agents')
    } finally {
      setLoading(false)
    }
  }, [agentId, navigate])

  const loadToken = useCallback(async () => {
    try {
      const data = await api.get(`/agents/${agentId}/token`)
      setTokenMasked(data.api_token)
    } catch { /* ignore */ }
  }, [agentId])

  const loadStorage = useCallback(async () => {
    try {
      const data = await api.get(`/agents/${agentId}/storage`)
      setStorage(data)
    } catch { /* ignore */ }
  }, [agentId])

  const loadLogs = useCallback(async () => {
    setLogsLoading(true)
    setLogsError(false)
    try {
      const data = await api.get(`/conversation-log/agents/${agentId}/logs?limit=50`)
      setLogs(data || [])
    } catch {
      setLogs([])
      setLogsError(true)
    }
    finally { setLogsLoading(false) }
  }, [agentId])

  const openLogDetail = async (logId: number) => {
    setLogDetailLoading(true)
    setSelectedLog(null)
    try {
      const detail = await api.get(`/conversation-log/agents/${agentId}/logs/${logId}`)
      setSelectedLog(detail)
    } catch { /* silently fail */ }
    finally { setLogDetailLoading(false) }
  }

  const exportLog = async (logId: number, format: 'json' | 'md') => {
    setLogExporting(true)
    try {
      await api.download(`/conversation-log/agents/${agentId}/logs/${logId}/export?format=${format}`,
                         `log-${logId}.${format === 'md' ? 'md' : 'json'}`)
    } catch { /* ignore */ }
    finally { setLogExporting(false) }
  }

  const loadMemories = useCallback(async (page: number) => {
    try {
      const data = await api.get(`/agents/${agentId}/memories?page=${page}&page_size=20`)
      setMemories(data.items)
      setMemTotal(data.total)
      setMemPage(data.page)
    } catch { /* ignore */ }
  }, [agentId])

  // 同步本地颜色状态
  useEffect(() => {
    setProfileStatusColor(agent?.status_color || '')
  }, [agent?.status_color])

  useEffect(() => {
    loadAgent()
    loadToken()
    // 加载模型选项（供完整设置弹窗使用）
    api.get<{ models: { value: string; label: string }[]; defaults: { chat_model: string; work_model: string }; thinking_supported: boolean }>('/agents/models')
      .then(data => {
        setModelOptions(data.models || [])
        setProvidersList((data as any).providers || [])
        setDefaultModels(data.defaults || { chat_model: '', work_model: '' })
        setThinkingSupported(data.thinking_supported !== false)
      })
      .catch(() => {})
  }, [loadAgent, loadToken])

  useEffect(() => {
    if (activeTab === 'storage') loadStorage()
    if (activeTab === 'logs') loadLogs()
    if (activeTab === 'memories') loadMemories(1)
    if (activeTab === 'workspace') loadWorkspace()
    if (activeTab === 'collaborators') loadCollaborators()
  }, [activeTab, loadStorage, loadLogs, loadMemories, loadWorkspace, loadCollaborators])

  // Delete handler
  const handleDelete = async () => {
    if (deleteConfirmName !== agent?.name) return
    setDeleting(true)
    try {
      await api.delete(`/agents/${agentId}`)
      refreshUser()
      navigate('/agents')
    } catch (err: any) {
      alert(err.message || t('error.saveFailed'))
    } finally {
      setDeleting(false)
    }
  }

  // 文件操作
  const handleOpenDeleteConfirm = async (file: { id: number; name: string }) => {
    setDeleteFileTarget(file)
    setDeleteFileRefs(null)
    setDeleteFileLoading(true)
    try {
      const data = await api.get<{ reference_count: number; references: FileReference[] }>(`/fs/${file.id}/references-detail`)
      setDeleteFileRefs(data)
    } catch {
      setDeleteFileRefs({ reference_count: 0, references: [] })
    } finally {
      setDeleteFileLoading(false)
    }
  }

  const handleDeleteFile = async () => {
    if (!deleteFileTarget) return
    setDeletingFile(true)
    try {
      await api.delete(`/fs/delete/${deleteFileTarget.id}`)
      setDeleteFileTarget(null)
      setDeleteFileRefs(null)
      loadStorage()
    } catch (err: any) {
      alert(err.message || err.detail || t('error.saveFailed'))
    } finally {
      setDeletingFile(false)
    }
  }

  // Prompt save
  const handleSavePrompt = async () => {
    setSavingPrompt(true)
    try {
      const data = await api.put(`/agents/${agentId}/config`, { system_prompt: promptText })
      setAgent(data)
      setEditingPrompt(false)
    } catch (err: any) {
      alert(err.message || t('error.saveFailed'))
    } finally {
      setSavingPrompt(false)
    }
  }

  // delay_reply_enabled toggle (auto-save)
  const handleToggleDelayReply = async (value: boolean | null) => {
    try {
      const data = await api.put(`/agents/${agentId}/config`, { delay_reply_enabled: value })
      setAgent(data)
    } catch (err: any) {
      alert(err.message || t('error.saveFailed'))
    }
  }

  const handleUpdateAgentField = async (field: string, value: any) => {
    try {
      const data = await api.put(`/agents/${agentId}/config`, { [field]: value })
      setAgent(data)
    } catch (err: any) {
      alert(err.message || t('error.saveFailed'))
    }
  }

  // Token generation
  const handleGenerateToken = async () => {
    setGeneratingToken(true)
    try {
      const data = await api.post(`/agents/${agentId}/token`, {})
      setToken(data.api_token)
      setTokenMasked(null) // refresh
      await loadToken()
    } catch (err: any) {
      alert(err.message || t('error.operationFailed'))
    } finally {
      setGeneratingToken(false)
    }
  }

  // Export
  const handleExport = async () => {
    try {
      await api.download(`/agents/${agentId}/export`, `soul_${agent?.name || 'agent'}.json`)
    } catch (err: any) {
      alert(t('error.exportFailed'))
    }
  }

  // Copy JSON
  const handleCopyExport = async () => {
    try {
      const token = localStorage.getItem('access_token')
      const res = await fetch(`/api/agents/${agentId}/export`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const text = await res.text()
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      alert(t('common.copied') + ' ' + t('error.operationFailed'))
    }
  }

  // Import
  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const token = localStorage.getItem('access_token')
      const res = await fetch(`/api/agents/import?import_memories=true`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || t('error.operationFailed'))
      }
      const data = await res.json()
      navigate(`/agents/${data.id}`)
    } catch (err: any) {
      alert(err.message || t('error.operationFailed'))
    } finally {
      setImporting(false)
    }
  }

  // Avatar upload via picker modal
  const handleAgentAvatarPickerUpload = async (blob: Blob) => {
    setUploadingAvatar(true)
    try {
      const ext = blob.type === 'image/gif' ? 'gif' : blob.type === 'image/png' ? 'png' : 'jpg'
      await api.upload(`/agents/${agentId}/avatar`, new File([blob], `avatar.${ext}`, { type: blob.type || 'image/jpeg' }))
      await loadAgent()
    } catch (err: any) {
      alert(err?.message || err?.detail || '上传失败')
    } finally {
      setUploadingAvatar(false)
    }
  }

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center bg-canvas">
        <div className="animate-spin w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full" />
      </div>
    )
  }

  if (!agent) return null

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 bg-canvas">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => navigate('/agents')} className="text-textMuted hover:text-textPrimary transition-colors">
            <ArrowLeft size={20} />
          </button>
          <div className="flex items-center gap-3 flex-1">
            <div className="w-12 h-12 rounded-card bg-primary-500/10 flex items-center justify-center overflow-hidden">
              {agent.avatar_url ? (
                <img src={agent.avatar_url} alt={agent.name} className="w-full h-full object-cover" />
              ) : (
                <Bot size={24} className="text-primary-400" />
              )}
            </div>
            <div>
              <h1 className="text-xl font-bold text-textPrimary">{agent.name}</h1>
              <div className="flex items-center gap-2 mt-0.5">
                <span className={`text-xs px-2 py-0.5 rounded-full border ${STATE_BADGE_COLORS[agent.state] || ''}`}>
                  {agent.state}
                </span>
                {agent.status_text && (
                  <span className="text-xs font-medium" style={agent.status_color
                    ? getStatusTextStyle(agent.status_color, '#151223')
                    : undefined}>
                    · {agent.status_text}
                  </span>
                )}
                {agent.ai_type && agent.ai_type !== 'resonance' && (
                  <span className="text-xs px-2 py-0.5 rounded-full border border-accent-400/40 bg-accent-400/10 text-accent-400">
                    {agent.ai_type === 'general' ? t('agentDetail.aiTypeGeneral') : t('agentDetail.aiTypeSemiGeneral')}
                  </span>
                )}
                <span className="text-xs text-textMuted">
                  {t('agentDetail.createdOn')} {new Date(agent.created_at).toLocaleDateString('zh-CN')}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-4 border-b border-border overflow-x-auto">
          {(['info', 'channels', 'memories', 'storage', 'workspace', 'logs', 'collaborators'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === tab
                  ? 'border-primary-500 text-primary-400'
                  : 'border-transparent text-textMuted hover:text-textSecondary'
              }`}
            >
              {tabLabels[tab]}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'info' && (
          <div className="space-y-4">
            {/* 完整设置入口 */}
            <button
              onClick={() => setShowFullSettings(true)}
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-card border-2 border-dashed border-primary-400/50 bg-primary-500/5 hover:bg-primary-500/10 text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 text-sm font-medium transition-colors"
            >
              <Settings size={16} />
              {t('agentDetail.fullSettings')}
              <span className="text-textMuted text-xs font-normal">— {t('agentDetail.fullSettingsDesc')}</span>
            </button>

            {/* Quick Edit Prompt */}
            <div className="bg-surface rounded-card border border-border p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Edit3 size={16} className="text-primary-400" />
                  <h3 className="font-medium text-textPrimary text-sm">{t('agentDetail.systemPrompt')}</h3>
                </div>
                {!editingPrompt ? (
                  <button
                    onClick={() => { setEditingPrompt(true); setPromptText(agent.current_system_prompt || '') }}
                    className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors"
                  >
                    {t('common.edit')}
                  </button>
                ) : (
                  <div className="flex gap-2">
                    <button onClick={() => setEditingPrompt(false)} className="text-xs text-textMuted hover:text-textSecondary">
                      <X size={14} />
                    </button>
                    <button onClick={handleSavePrompt} disabled={savingPrompt} className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300">
                      {savingPrompt ? '...' : <Check size={14} />}
                    </button>
                  </div>
                )}
              </div>
              {editingPrompt ? (
                <textarea
                  value={promptText}
                  onChange={(e) => setPromptText(e.target.value)}
                  rows={6}
                  className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-y"
                />
              ) : (
                <p className="text-sm text-textSecondary leading-relaxed whitespace-pre-wrap">
                  {agent.current_system_prompt || t('agentDetail.notSet')}
                </p>
              )}
            </div>

            {/* 个人资料 */}
            <div className="bg-surface rounded-card border border-border p-4">
              <div className="flex items-center gap-2 mb-3">
                <User size={16} className="text-primary-400" />
                <h3 className="font-medium text-textPrimary text-sm">{t('agentDetail.profileSection')}</h3>
              </div>
              <div className="space-y-3">
                <div>
                  <label className="text-xs text-textMuted">{t('agentDetail.bioLabel')}</label>
                  <div className="relative mt-1">
                    <textarea
                      defaultValue={agent?.bio || ''}
                      onBlur={(e) => {
                        const val = e.target.value
                        if (val !== (agent?.bio || '')) handleUpdateAgentField('bio', val || null)
                      }}
                      rows={3}
                      maxLength={500}
                      className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-y"
                      placeholder={t('agentDetail.bioPlaceholder')}
                    />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-textMuted">{t('agentDetail.statusTextLabel')}</label>
                  <input
                    type="text"
                    defaultValue={agent?.status_text || ''}
                    onBlur={(e) => {
                      const val = e.target.value
                      if (val !== (agent?.status_text || '')) handleUpdateAgentField('status_text', val || null)
                    }}
                    className="w-full mt-1 px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                    placeholder={t('agentDetail.statusTextPlaceholder')}
                  />
                  <p className="text-3xs text-textMuted mt-1">{t('agentDetail.statusTextHint')}</p>
                </div>
                <div>
                  <label className="text-xs text-textMuted">{t('me.statusColorLabel')}</label>
                  <div className="flex items-center gap-2 flex-wrap mt-1">
                    {STATUS_COLORS.map(c => (
                      <button key={c.value} type="button"
                        onClick={() => { setProfileStatusColor(c.value); handleUpdateAgentField('status_color', c.value || null) }}
                        className={`w-6 h-6 rounded-full border-2 transition-all ${
                          (profileStatusColor || '') === c.value
                            ? 'border-primary-400 scale-110 shadow-md'
                            : c.value === ''
                              ? 'border-border bg-canvas hover:border-textMuted'
                              : 'border-transparent hover:scale-105'
                        }`}
                        style={c.value ? { backgroundColor: c.value } : undefined}
                        title={c.label}
                      >
                        {c.value === '' && <X size={10} className="text-textMuted m-auto" />}
                      </button>
                    ))}
                    <div className="relative">
                      <input type="color" value={profileStatusColor || '#000000'}
                        onChange={e => setProfileStatusColor(e.target.value)}
                        onBlur={e => {
                          if (e.target.value !== (agent?.status_color || '')) {
                            handleUpdateAgentField('status_color', e.target.value || null)
                          }
                        }}
                        className="w-6 h-6 rounded-full cursor-pointer border-2 border-border hover:border-primary-400 transition-colors"
                        title={t('me.statusColorCustom')} />
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Config Info */}
            <div className="bg-surface rounded-card border border-border p-4">
              <h3 className="font-medium text-textPrimary text-sm mb-3">{t('agentDetail.modelConfig')}</h3>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-textMuted">{t('agentDetail.chatModel')}</span>
                  <span className="text-textPrimary">{agent.chat_model || t('common.default')}</span>
                </div>
                <div>
                  <span className="text-textMuted">{t('agentDetail.workModel')}</span>
                  <span className="text-textPrimary">{agent.work_model || t('common.default')}</span>
                </div>
                <div>
                  <span className="text-textMuted">{t('agentDetail.temperature')}</span>
                  <span className="text-textPrimary">{agent.current_temperature}</span>
                </div>
                <div>
                  <span className="text-textMuted">{t('agentDetail.topP')}</span>
                  <span className="text-textPrimary">{agent.current_top_p}</span>
                </div>
                <div>
                  <span className="text-textMuted">{t('agentDetail.thinking')}</span>
                  <span className="text-textPrimary">{agent.thinking_enabled ? t('agentDetail.enabled') : t('agentDetail.disabled')}</span>
                </div>
                <div>
                  <span className="text-textMuted">{t('agentDetail.aiIdentity')}</span>
                  <span className="text-textPrimary">{agent.hide_ai_identity ? t('agentDetail.hidden') : t('agentDetail.normal')}</span>
                </div>
                <div>
                  <span className="text-textMuted">{t('agentDetail.configProfile')}</span>
                  <span className="text-textPrimary">
                    {agent.config_profile === 'chat' ? t('agentDetail.profileChat') :
                     agent.config_profile === 'immersive' ? t('agentDetail.profileImmersive') :
                     t('agentDetail.profileDigitalLife')}
                  </span>
                </div>
                <div>
                  <span className="text-textMuted">{t('agentDetail.delayReply')}</span>
                  <select
                    value={agent.delay_reply_enabled === null ? 'inherit' : agent.delay_reply_enabled ? 'on' : 'off'}
                    onChange={(e) => {
                      const v = e.target.value
                      handleToggleDelayReply(v === 'inherit' ? null : v === 'on')
                    }}
                    className="text-xs px-2 py-0.5 rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                  >
                    <option value="inherit">{t('agentDetail.inheritGlobal')}</option>
                    <option value="on">{t('agentDetail.enabled')}</option>
                    <option value="off">{t('agentDetail.disabled')}</option>
                  </select>
                </div>
              </div>
              {/* 工具调用 & 闹钟 */}
              <div className="mt-4 pt-4 border-t border-border/60">
                <h4 className="text-xs font-medium text-textSecondary mb-3">{t('agentDetail.toolCallsAndAlarms')}</h4>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <span className="text-textMuted">{t('agentDetail.maxToolRounds')}</span>
                    <span className="inline-flex items-center gap-1 ml-1">
                      <button
                        onClick={() => handleUpdateAgentField('max_tool_rounds', Math.max(1, (agent.max_tool_rounds || 3) - 1))}
                        className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs"
                      >−</button>
                      <span className="text-textPrimary font-mono w-5 text-center">{agent.max_tool_rounds || 3}</span>
                      <button
                        onClick={() => handleUpdateAgentField('max_tool_rounds', Math.min(20, (agent.max_tool_rounds || 3) + 1))}
                        className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs"
                      >+</button>
                    </span>
                  </div>
                  <div>
                    <span className="text-textMuted">{t('agentDetail.alarmMaxRounds')}</span>
                    <span className="inline-flex items-center gap-1 ml-1">
                      <button
                        onClick={() => handleUpdateAgentField('alarm_max_tool_rounds', Math.max(1, (agent.alarm_max_tool_rounds || 10) - 1))}
                        className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs"
                      >−</button>
                      <span className="text-textPrimary font-mono w-5 text-center">{agent.alarm_max_tool_rounds || 10}</span>
                      <button
                        onClick={() => handleUpdateAgentField('alarm_max_tool_rounds', Math.min(50, (agent.alarm_max_tool_rounds || 10) + 1))}
                        className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs"
                      >+</button>
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-textMuted">{t('agentDetail.forceAlarm')}</span>
                    <Toggle checked={agent.force_alarm_on_end} onChange={(v) => handleUpdateAgentField('force_alarm_on_end', v)} />
                  </div>
                  <div>
                    <span className="text-textMuted">{t('agentDetail.maxAlarms')}</span>
                    <span className="inline-flex items-center gap-1 ml-1">
                      <button
                        onClick={() => handleUpdateAgentField('max_alarms', Math.max(1, (agent.max_alarms || 10) - 1))}
                        className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs"
                      >−</button>
                      <span className="text-textPrimary font-mono w-5 text-center">{agent.max_alarms || 10}</span>
                      <button
                        onClick={() => handleUpdateAgentField('max_alarms', Math.min(50, (agent.max_alarms || 10) + 1))}
                        className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs"
                      >+</button>
                    </span>
                  </div>
                </div>
              </div>
              {/* 文件记忆 */}
              <div className="mt-4 pt-4 border-t border-border/60">
                <h4 className="text-xs font-medium text-textSecondary mb-3">{t('agentDetail.fileMemory')}</h4>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <span className="text-textMuted">{t('agentDetail.memoryLoadMode')}</span>
                    <select
                      value={agent.memory_load_mode || 'index_only'}
                      onChange={(e) => handleUpdateAgentField('memory_load_mode', e.target.value)}
                      className="text-xs px-2 py-0.5 rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50 ml-1"
                    >
                      <option value="index_only">{t('agentDetail.memoryModeIndexOnly')}</option>
                      <option value="index_plus_recent">{t('agentDetail.memoryModeIndexRecent')}</option>
                      <option value="index_plus_semantic">{t('agentDetail.memoryModeIndexSemantic')}</option>
                    </select>
                  </div>
                  <div>
                    <span className="text-textMuted">{t('agentDetail.memoryRecentCount')}</span>
                    <span className="inline-flex items-center gap-1 ml-1">
                      <button
                        onClick={() => handleUpdateAgentField('memory_recent_count', Math.max(0, (agent.memory_recent_count || 0) - 1))}
                        className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs"
                      >−</button>
                      <span className="text-textPrimary font-mono w-5 text-center">{agent.memory_recent_count || 0}</span>
                      <button
                        onClick={() => handleUpdateAgentField('memory_recent_count', Math.min(50, (agent.memory_recent_count || 0) + 1))}
                        className="w-5 h-5 rounded bg-canvas border border-border text-textMuted hover:text-textPrimary text-xs"
                      >+</button>
                    </span>
                  </div>
                  <div className="col-span-2">
                    <span className="text-textMuted">{t('agentDetail.memorySharedScope')}</span>
                    <select
                      value={agent.memory_shared_scope || 'private_only'}
                      onChange={(e) => handleUpdateAgentField('memory_shared_scope', e.target.value)}
                      className="text-xs px-2 py-0.5 rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50 ml-1"
                    >
                      <option value="private_only">{t('agentDetail.sharedScopePrivate')}</option>
                      <option value="private_plus_shared_by_user">{t('agentDetail.sharedScopeByUser')}</option>
                      <option value="private_plus_shared_all">{t('agentDetail.sharedScopeAll')}</option>
                    </select>
                  </div>
                </div>
              </div>
              {/* 好友与社交 */}
              <div className="mt-4 pt-4 border-t border-border/60">
                <h4 className="text-xs font-medium text-textSecondary mb-3">{t('agentDetail.friendsAndSocial')}</h4>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="text-textMuted">{t('agentDetail.allowFriendRequests')}</span>
                    <Toggle checked={agent.allow_friend_requests} onChange={(v) => handleUpdateAgentField('allow_friend_requests', v)} />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-textMuted">{t('agentDetail.autoRespondRequests')}</span>
                    <Toggle checked={agent.auto_respond_friend_request} onChange={(v) => handleUpdateAgentField('auto_respond_friend_request', v)} disabled={!agent.allow_friend_requests} />
                  </div>
                </div>
              </div>
            </div>

            {/* API Config */}
            <div className="bg-surface rounded-card border border-border p-4">
              <div className="flex items-center gap-2 mb-3">
                <Key size={16} className="text-primary-400" />
                <h3 className="font-medium text-textPrimary text-sm">{t('agentDetail.apiConfig')}</h3>
              </div>
              <div className="text-sm space-y-1">
                <p>
                  <span className="text-textMuted">{t('agentDetail.apiBaseUrl')}</span>
                  <span className="text-textPrimary">{agent.api_base_url || t('agentDetail.inheritGlobal')}</span>
                </p>
                <p>
                  <span className="text-textMuted">{t('agentDetail.apiKey')}</span>
                  <span className="text-textPrimary">{agent.has_api_key ? t('agentDetail.apiKeySet') : t('agentDetail.inheritGlobal')}</span>
                </p>
                <p>
                  <span className="text-textMuted">{t('agentDetail.apiCreditCost')}</span>
                  <span className="text-textPrimary">{agent.api_credit_cost}</span>
                </p>
              </div>
            </div>

            {/* Actions */}
            <div className="bg-surface rounded-card border border-border p-4">
              <h3 className="font-medium text-textPrimary text-sm mb-3">{t('agentDetail.actions')}</h3>
              <div className="flex flex-wrap gap-2">
                {/* Export */}
                <button onClick={handleExport} className="btn btn-sm btn-outline gap-1.5 hover:text-textPrimary hover:border-primary-500/30">
                  <Download size={14} /> {t('agentDetail.downloadExport')}
                </button>
                <button onClick={handleCopyExport} className="btn btn-sm btn-outline gap-1.5 hover:text-textPrimary hover:border-primary-500/30">
                  {copied ? <Check size={14} className="text-mint-400" /> : <Copy size={14} />}
                  {copied ? t('agentDetail.copied') : t('agentDetail.copyJson')}
                </button>

                {/* Import */}
                <label className="flex items-center gap-1.5 px-3 py-2 rounded-control border border-border text-sm text-textSecondary hover:text-textPrimary hover:border-primary-500/30 transition-colors cursor-pointer">
                  <Upload size={14} />
                  {importing ? t('common.saving') : t('agentDetail.importSoul')}
                  <input type="file" accept=".json" onChange={handleImport} className="hidden" />
                </label>

                {/* Avatar */}
                <button
                  onClick={() => setAvatarPickerOpen(true)}
                  disabled={uploadingAvatar}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-control border border-border text-sm text-textSecondary hover:text-textPrimary hover:border-primary-500/30 transition-colors disabled:opacity-50"
                >
                  <Image size={14} />
                  {uploadingAvatar ? t('me.uploadingAvatar') : t('agentDetail.changeAvatar')}
                </button>

                {/* Token */}
                <button onClick={handleGenerateToken} disabled={generatingToken} className="btn btn-sm btn-outline gap-1.5 hover:text-textPrimary hover:border-primary-500/30">
                  <RefreshCw size={14} className={generatingToken ? 'animate-spin' : ''} />
                  {tokenMasked ? `Token: ${tokenMasked}` : t('agentDetail.generateToken')}
                </button>
                {token && (
                  <div className="w-full flex items-center gap-2 mt-2 p-2 rounded-control bg-accent-400/5 border border-accent-400/20">
                    <code className="text-xs text-accent-400 flex-1 break-all">{token}</code>
                    <button
                      onClick={() => { navigator.clipboard.writeText(token); setToken(null) }}
                      className="text-xs text-accent-400 hover:text-accent-500 dark:hover:text-accent-300"
                    >
                      {t('agentDetail.copyOnce')}
                    </button>
                  </div>
                )}

                {/* Delete */}
                <button onClick={() => setShowDelete(true)} className="flex items-center gap-1.5 px-3 py-2 rounded-control border border-rose-500/20 text-sm text-rose-400 hover:bg-rose-500/10 transition-colors">
                  <Trash2 size={14} /> {t('agentDetail.deleteAgent')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 通道：这个 AI 对外的接线（QQ 等）。这里直接铺开，不用再开弹窗 */}
        {activeTab === 'channels' && (
          <div className="max-w-3xl">
            <QQChannelCard agentId={agent.id} />
          </div>
        )}

        {activeTab === 'storage' && (
          <div className="bg-surface rounded-card border border-border p-4">
            <div className="flex items-center gap-2 mb-4">
              <HardDrive size={16} className="text-primary-400" />
              <h3 className="font-medium text-textPrimary text-sm">{t('agentDetail.storageTitle')}</h3>
            </div>
            {storage ? (
              <>
                {/* 进度条 */}
                <div className="mb-4">
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="text-textSecondary">
                      {t('agentDetail.storageUsed')} {formatSize(storage.total_size)} / {storage.quota_mb}MB
                    </span>
                    <span className={`font-medium ${
                      storage.usage_percent > 90 ? 'text-rose-400' :
                      storage.usage_percent > 70 ? 'text-accent-400' :
                      'text-mint-400'
                    }`}>
                      {storage.usage_percent}%
                    </span>
                  </div>
                  <div className="w-full h-3 rounded-full bg-canvas border border-border overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        storage.usage_percent > 90 ? 'bg-rose-400' :
                        storage.usage_percent > 70 ? 'bg-accent-400' :
                        'bg-mint-400'
                      }`}
                      style={{ width: `${Math.min(storage.usage_percent, 100)}%` }}
                    />
                  </div>
                  {storage.usage_percent > 90 && (
                    <p className="text-xs text-rose-400 mt-1">{t('agentDetail.storageAlmostFull')}</p>
                  )}
                </div>

                {/* 统计卡片 */}
                <div className="flex gap-3 mb-4 text-sm">
                  <div className="flex-1 px-3 py-2 rounded-control bg-canvas border border-border text-center">
                    <div className="text-textMuted text-xs">{t('agentDetail.fileCountLabel')}</div>
                    <div className="text-textPrimary font-semibold">{storage.file_count}</div>
                  </div>
                  <div className="flex-1 px-3 py-2 rounded-control bg-canvas border border-border text-center">
                    <div className="text-textMuted text-xs">{t('agentDetail.totalSize')}</div>
                    <div className="text-textPrimary font-semibold">{formatSize(storage.total_size)}</div>
                  </div>
                  <div className="flex-1 px-3 py-2 rounded-control bg-canvas border border-border text-center">
                    <div className="text-textMuted text-xs">{t('agentDetail.quotaLabel')}</div>
                    <div className="text-textPrimary font-semibold">{storage.quota_mb}MB</div>
                  </div>
                </div>

                {storage.files.length > 0 ? (
                  <div className="space-y-1 max-h-72 overflow-y-auto">
                    {storage.files.map((f) => (
                      <div key={f.id} className="flex items-center justify-between text-xs py-2 px-2 rounded hover:bg-canvas group">
                        <button
                          onClick={() => setPreviewFile({ id: f.id, name: f.name, size: f.size, mime: getMimeType(f) })}
                          className="text-textSecondary hover:text-primary-400 truncate flex-1 mr-2 transition-colors text-left"
                          title={t('agentDetail.clickToPreview')}
                        >
                          {f.name}
                        </button>
                        <span className="text-textMuted shrink-0 mr-2">{formatSize(f.size)}</span>
                        <button
                          onClick={() => handleOpenDeleteConfirm(f)}
                          className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-rose-500/10 text-textMuted hover:text-rose-400 transition-all"
                          title={t('common.delete')}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-textMuted">{t('agentDetail.noFiles')}</p>
                )}
              </>
            ) : (
              <p className="text-sm text-textMuted">{t('agentDetail.storageLoading')}</p>
            )}
          </div>
        )}

        {activeTab === 'logs' && (
          <div className="bg-surface rounded-card border border-border p-4">
            <div className="flex items-center gap-2 mb-3">
              <ScrollText size={16} className="text-primary-400" />
              <h3 className="font-medium text-textPrimary text-sm">{t('agentDetail.logTitle')}</h3>
              {logs.length > 0 && (
                <span className="text-xs text-textMuted ml-auto">{logs.length} {t('agentDetail.logCountSuffix')}</span>
              )}
            </div>
            {logsLoading ? (
              <div className="flex items-center gap-2 text-sm text-textMuted py-4 justify-center">
                <Loader2 size={14} className="animate-spin" /> {t('agentDetail.storageLoading')}
              </div>
            ) : logs.length === 0 ? (
              <p className="text-sm text-textMuted py-4 text-center">
                {logsError ? (
                  <>
                    {t('agentDetail.logsEmpty')}<br />
                    <span className="text-xs">{t('agentDetail.logsEmptyHint')}</span>
                  </>
                ) : (
                  t('agentDetail.logsNone')
                )}
              </p>
            ) : (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {logs.map((log) => (
                  <div key={log.id} className="p-3 rounded-control bg-canvas border border-border">
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs px-1.5 py-0.5 rounded bg-primary-500/10 text-primary-400">
                          {log.conversation_type === 'dm' ? t('agentDetail.logTypeDm') : t('agentDetail.logTypeGroup')}
                        </span>
                        <span className="text-xs text-textMuted">
                          {log.message_count} {t('agentDetail.logMessageCount')}
                        </span>
                        {log.has_output && (
                          <span className="text-xs px-1.5 py-0.5 rounded bg-mint-500/10 text-mint-400">
                            {t('agentDetail.logHasOutput')}
                          </span>
                        )}
                        {log.thinking_enabled && (
                          <span className="text-xs px-1.5 py-0.5 rounded bg-accent-500/10 text-accent-400">
                            {t('agentDetail.logThinking')}
                          </span>
                        )}
                      </div>
                      <span className="text-xs text-textMuted">
                        {log.created_at ? new Date(log.created_at).toLocaleString('zh-CN') : ''}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 mt-2">
                      <button
                        onClick={() => openLogDetail(log.id)}
                        className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors"
                      >
                        {t('agentDetail.logViewDetail')}
                      </button>
                      <button
                        onClick={() => exportLog(log.id, 'json')}
                        disabled={logExporting}
                        className="text-xs text-textMuted hover:text-textSecondary transition-colors"
                      >
                        {t('agentDetail.logDownloadJson')}
                      </button>
                      <button
                        onClick={() => exportLog(log.id, 'md')}
                        disabled={logExporting}
                        className="text-xs text-textMuted hover:text-textSecondary transition-colors"
                      >
                        {t('agentDetail.logDownloadMd')}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* 日志详情弹窗 */}
            {selectedLog && (
              <Dialog onClose={() =>  setSelectedLog(null)} className="flex items-center justify-center">
                <div
                  className="bg-surface rounded-card border border-border max-w-2xl w-full mx-4 max-h-[80vh] flex flex-col shadow-2xl"
                  onClick={e => e.stopPropagation()}
                >
                  <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                    <h4 className="font-medium text-sm text-textPrimary">
                      {t('agentDetail.logDetailTitle')} #{selectedLog.id}
                      <span className="text-textMuted ml-2 text-xs">
                        {selectedLog.created_at ? new Date(selectedLog.created_at).toLocaleString('zh-CN') : ''}
                      </span>
                    </h4>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => exportLog(selectedLog.id, 'json')}
                        className="text-xs px-2 py-1 rounded border border-border hover:bg-canvas text-textSecondary transition-colors"
                      >
                        {t('agentDetail.logDownloadJson')}
                      </button>
                      <button
                        onClick={() => exportLog(selectedLog.id, 'md')}
                        className="text-xs px-2 py-1 rounded border border-border hover:bg-canvas text-textSecondary transition-colors"
                      >
                        {t('agentDetail.logDownloadMd')}
                      </button>
                      <button onClick={() => setSelectedLog(null)} className="p-1 rounded hover:bg-elevated text-textMuted">
                        <X size={16} />
                      </button>
                    </div>
                  </div>
                  <div className="flex-1 overflow-y-auto p-4 space-y-2">
                    {(selectedLog.messages || []).map((msg: any, i: number) => (
                      <div key={i} className={`text-xs p-2 rounded ${
                        msg.role === 'system' ? 'bg-canvas text-textMuted italic' :
                        msg.role === 'user' ? 'bg-primary-500/10 text-primary-600 dark:text-primary-300' :
                        msg.role === 'assistant' ? 'bg-mint-500/10 text-mint-500 dark:text-mint-400' :
                        'bg-accent-500/10 text-accent-600 dark:text-accent-300'
                      }`}>
                        <span className="font-medium">{msg.role}</span>
                        {msg.reasoning_content && (
                          <details className="group/details mt-1">
                            <summary className="flex items-center gap-1 text-textMuted cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                              <ChevronRight size={12} className="transition-transform group-open/details:rotate-90" />
                              {t('agentDetail.logReasoning')}
                            </summary>
                            <pre className="mt-1 whitespace-pre-wrap text-textMuted">{msg.reasoning_content}</pre>
                          </details>
                        )}
                        <p className="mt-1 whitespace-pre-wrap">
                          {typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)}
                        </p>
                        {msg.tool_calls && (
                          <div className="mt-1 text-textMuted">
                            {t('agentDetail.logToolCalls')} {msg.tool_calls.map((tc: any) => tc.function?.name || tc.name || '?').join(', ')}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </Dialog>
            )}
          </div>
        )}

        {activeTab === 'collaborators' && (
          <div className="space-y-4">
            {/* 非 owner 的合作者提示 */}
            {!isOwner && agent && user && agent.owner_id !== user.id && (
              <div className="bg-primary-500/10 border border-primary-500/20 rounded-card p-3 text-sm text-primary-500">
                {t('agentDetail.collaboratorNote')}
              </div>
            )}

            {/* Owner: 添加合作者 */}
            {isOwner && (
              <div className="bg-surface rounded-card border border-border p-4">
                <h3 className="font-medium text-textPrimary text-sm mb-3">{t('agentDetail.addCollaborator')}</h3>
                <div className="flex gap-2 mb-3">
                  <input
                    type="text"
                    value={collabSearch}
                    onChange={e => { setCollabSearch(e.target.value); searchUsers(e.target.value) }}
                    placeholder={t('agentDetail.searchUserPlaceholder')}
                    className="flex-1 px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  />
                </div>
                {/* 权限勾选 */}
                <div className="flex flex-wrap gap-3 mb-3">
                  <label className="flex items-center gap-1.5 text-xs text-textSecondary">
                    <input type="checkbox" checked={newCollabPerms.can_edit}
                      onChange={e => setNewCollabPerms(p => ({ ...p, can_edit: e.target.checked }))}
                      className="rounded" />
                    {t('agentDetail.permEdit')}
                  </label>
                  <label className="flex items-center gap-1.5 text-xs text-textSecondary">
                    <input type="checkbox" checked={newCollabPerms.can_delete}
                      onChange={e => setNewCollabPerms(p => ({ ...p, can_delete: e.target.checked }))}
                      className="rounded" />
                    {t('agentDetail.permDelete')}
                  </label>
                  <label className="flex items-center gap-1.5 text-xs text-textSecondary">
                    <input type="checkbox" checked={newCollabPerms.can_manage_collaborators}
                      onChange={e => setNewCollabPerms(p => ({ ...p, can_manage_collaborators: e.target.checked }))}
                      className="rounded" />
                    {t('agentDetail.permManageCollaborators')}
                  </label>
                </div>
                {/* 搜索结果 */}
                {collabSearchResults.length > 0 && (
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {collabSearchResults.map(u => (
                      <div key={u.id} className="flex items-center justify-between px-3 py-2 rounded-control bg-canvas border border-border/60">
                        <div className="flex items-center gap-2">
                          {u.avatar_url ? (
                            <img src={u.avatar_url} alt="" className="w-6 h-6 rounded-full object-cover" />
                          ) : (
                            <div className="w-6 h-6 rounded-full bg-elevated flex items-center justify-center text-xs text-textMuted">?</div>
                          )}
                          <span className="text-sm text-textPrimary">{u.username}</span>
                        </div>
                        <button
                          onClick={() => handleAddCollaborator(u.id)}
                          disabled={addingCollab}
                          className="px-3 py-1 rounded-control bg-mint-400 text-white text-xs hover:bg-mint-500 disabled:opacity-40 transition-colors"
                        >
                          {addingCollab ? '...' : t('agentDetail.addCollaboratorBtn')}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 合作者列表 */}
            {collaborators.length > 0 && (
              <div className="bg-surface rounded-card border border-border p-4">
                <h3 className="font-medium text-textPrimary text-sm mb-3">{t('agentDetail.collaboratorList')} ({collaborators.length})</h3>
                <div className="space-y-2">
                  {collaborators.map(c => (
                    <div key={c.id} className="flex items-center justify-between px-3 py-2 rounded-control bg-canvas border border-border/60">
                      <div className="flex items-center gap-2">
                        {c.avatar_url ? (
                          <img src={c.avatar_url} alt="" className="w-7 h-7 rounded-full object-cover" />
                        ) : (
                          <div className="w-7 h-7 rounded-full bg-primary-500/10 flex items-center justify-center text-xs text-primary-400 font-medium">
                            {(c.username || '?')[0].toUpperCase()}
                          </div>
                        )}
                        <div>
                          <span className="text-sm text-textPrimary">{c.username || `ID:${c.user_id}`}</span>
                          <div className="flex gap-1 mt-0.5">
                            {c.can_edit && <span className="text-3xs px-1.5 py-0.5 rounded bg-mint-400/10 text-mint-400 border border-mint-400/20">{t('agentDetail.permEdit')}</span>}
                            {c.can_delete && <span className="text-3xs px-1.5 py-0.5 rounded bg-rose-400/10 text-rose-400 border border-rose-400/20">{t('agentDetail.permDelete')}</span>}
                            {c.can_manage_collaborators && <span className="text-3xs px-1.5 py-0.5 rounded bg-accent-400/10 text-accent-400 border border-accent-400/20">{t('agentDetail.permManageCollaborators')}</span>}
                          </div>
                        </div>
                      </div>
                      {isOwner && (
                        <div className="flex items-center gap-2">
                          <div className="flex items-center gap-1">
                            <span className="text-3xs text-textMuted">{t('agentDetail.permEdit')}</span>
                            <Toggle checked={c.can_edit} onChange={v => handleUpdateCollaborator(c.user_id, { can_edit: v })} />
                          </div>
                          <div className="flex items-center gap-1">
                            <span className="text-3xs text-textMuted">{t('agentDetail.permDelete')}</span>
                            <Toggle checked={c.can_delete} onChange={v => handleUpdateCollaborator(c.user_id, { can_delete: v })} />
                          </div>
                          <div className="flex items-center gap-1">
                            <span className="text-3xs text-textMuted">{t('agentDetail.permManageCollaborators')}</span>
                            <Toggle checked={c.can_manage_collaborators} onChange={v => handleUpdateCollaborator(c.user_id, { can_manage_collaborators: v })} />
                          </div>
                          <button
                            onClick={() => handleRemoveCollaborator(c.user_id)}
                            className="ml-1 p-1 rounded text-textMuted hover:text-rose-400 transition-colors"
                            title={t('agentDetail.removeCollaborator')}
                          >
                            <X size={14} />
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 空状态 */}
            {collaborators.length === 0 && isOwner && (
              <div className="text-center py-8 text-textMuted text-sm">
                {t('agentDetail.noCollaborators')}
              </div>
            )}
          </div>
        )}

        {activeTab === 'memories' && (
          <div className="bg-surface rounded-card border border-border p-4">
            <div className="flex items-center gap-2 mb-3">
              <Brain size={16} className="text-primary-400" />
              <h3 className="font-medium text-textPrimary text-sm">{t('agentDetail.memoryTitlePrefix')}{memTotal}{t('agentDetail.memoryTitleSuffix')}</h3>
              <div className="flex gap-0.5 ml-auto bg-canvas rounded-control p-0.5 border border-border">
                {(['structured', 'vector'] as const).map((st) => (
                  <button
                    key={st}
                    onClick={() => { setMemSubTab(st); if (st === 'vector') loadMemories(1) }}
                    className={`px-2.5 py-1 text-xs font-medium rounded-control transition-colors ${
                      memSubTab === st ? 'bg-primary-500 text-white shadow-sm' : 'text-textMuted hover:text-textSecondary'
                    }`}
                  >
                    {st === 'structured' ? t('agentDetail.structuredMemories') : t('agentDetail.vectorMemories')}
                  </button>
                ))}
              </div>
            </div>

            {memSubTab === 'structured' ? (
              <StructuredMemoryView agentId={agentId} />
            ) : (
              <>
                {memories.length > 0 ? (
                  <div className="space-y-1.5 max-h-96 overflow-y-auto">
                    {memories.map((m) => (
                      <VectorMemoryCard key={m.id} mem={m} t={t} />
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-textMuted">{t('agentDetail.noMemories')}</p>
                )}
                {memTotal > 20 && (
                  <div className="flex justify-center gap-2 mt-4">
                    <button
                      onClick={() => loadMemories(memPage - 1)}
                      disabled={memPage <= 1}
                      className="px-3 py-1 text-xs rounded-control border border-border text-textSecondary hover:text-textPrimary disabled:opacity-30"
                    >
                      {t('agentDetail.prevPage')}
                    </button>
                    <span className="text-xs text-textMuted py-1">{memPage} / {Math.ceil(memTotal / 20)}</span>
                    <button
                      onClick={() => loadMemories(memPage + 1)}
                      disabled={memPage >= Math.ceil(memTotal / 20)}
                      className="px-3 py-1 text-xs rounded-control border border-border text-textSecondary hover:text-textPrimary disabled:opacity-30"
                    >
                      {t('agentDetail.nextPage')}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {activeTab === 'workspace' && (
          <div className="bg-surface rounded-card border border-border p-4">
            <div className="flex items-center gap-2 mb-3">
              <Edit3 size={16} className="text-primary-400" />
              <h3 className="font-medium text-textPrimary text-sm">{t('agentDetail.workspaceTitle')}</h3>
              <span className="text-xs text-textMuted ml-auto">{t('agentDetail.workspaceHint')}</span>
            </div>
            {/* Sub-tabs */}
            <div className="flex gap-1 mb-3 border-b border-border">
              {(['todo', 'plan', 'journal'] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setWsActive(f)}
                  className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
                    wsActive === f
                      ? 'border-primary-500 text-primary-400'
                      : 'border-transparent text-textMuted hover:text-textSecondary'
                  }`}
                >
                  {f === 'todo' ? t('agentDetail.workspaceTodoLabel') : f === 'plan' ? t('agentDetail.workspacePlanLabel') : t('agentDetail.workspaceJournalLabel')}
                </button>
              ))}
            </div>
            {/* Content */}
            <pre className="text-xs text-textSecondary whitespace-pre-wrap max-h-80 overflow-y-auto p-3 rounded-control bg-canvas border border-border leading-relaxed font-mono min-h-[120px]">
              {workspace[wsActive] || `（${wsActive === 'todo' ? t('agentDetail.workspaceTodoLabel') : wsActive === 'plan' ? t('agentDetail.workspacePlanLabel') : t('agentDetail.workspaceJournalLabel')} ${t('agentDetail.workspaceEmptySuffix')}`}
            </pre>
          </div>
        )}
      </div>

      {/* Delete Confirm Modal */}
      {showDelete && (
        <div className="fixed inset-0 z-modal flex items-center justify-center bg-black/50">
          <div className="bg-surface rounded-dialog border border-border p-6 w-full max-w-sm mx-4 pb-[var(--safe-bottom)] md:pb-6">
            <h3 className="text-lg font-bold text-textPrimary mb-2">{t('agentDetail.deleteConfirmTitle')}</h3>
            <p className="text-sm text-textSecondary mb-4">
              {t('agentDetail.deleteConfirmText')}<span className="text-mint-400 font-medium">{agent.api_credit_cost}</span> {t('agentDetail.deleteConfirmText2')}
              <span className="text-rose-400 font-medium">"{agent.name}"</span> {t('agentDetail.deleteConfirm')}
            </p>
            <input
              type="text"
              value={deleteConfirmName}
              onChange={(e) => setDeleteConfirmName(e.target.value)}
              className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-rose-500/50 mb-4"
              placeholder={agent.name}
            />
            <div className="flex gap-3">
              <button
                onClick={() => { setShowDelete(false); setDeleteConfirmName('') }}
                className="flex-1 px-4 py-2 rounded-card border border-border text-sm text-textSecondary hover:text-textPrimary"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleDelete}
                disabled={deleteConfirmName !== agent.name || deleting}
                className="flex-1 px-4 py-2 rounded-card bg-rose-500 text-white text-sm font-medium hover:bg-rose-400 disabled:opacity-30 transition-colors"
              >
                {deleting ? t('agentDetail.deleting') : t('agentDetail.confirmDelete')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 头像选择弹窗 */}
      {avatarPickerOpen && (
        <AvatarPickerModal
          onUpload={handleAgentAvatarPickerUpload}
          onClose={() => setAvatarPickerOpen(false)}
          title={t('agentDetail.changeAvatar')}
        />
      )}

      {/* 文件预览弹窗 */}
      {previewFile && (
        <FilePreviewModal
          fileId={previewFile.id}
          fileName={previewFile.name}
          fileSize={previewFile.size}
          mimeType={previewFile.mime}
          onClose={() => setPreviewFile(null)}
        />
      )}

      {/* 文件删除确认弹窗 */}
      {deleteFileTarget && (
        <Dialog onClose={() =>  { setDeleteFileTarget(null); setDeleteFileRefs(null) } } layer="toast" className="flex items-center justify-center p-4">
          <div className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-4">
              <div className="w-8 h-8 rounded-full bg-rose-500/10 flex items-center justify-center">
                <Trash2 size={16} className="text-rose-400" />
              </div>
              <h3 className="font-semibold text-textPrimary text-base">{t('agentDetail.deleteFileTitle')}</h3>
            </div>

            <p className="text-sm text-textSecondary mb-3">
              {t('agentDetail.deleteFileConfirm')} <span className="font-medium text-textPrimary">「{deleteFileTarget.name}」</span>？
            </p>

            {/* 引用信息 */}
            <div className="bg-canvas rounded-control p-3 mb-4 text-xs">
              {deleteFileLoading ? (
                <div className="flex items-center gap-2 text-textMuted py-2">
                  <Loader2 size={14} className="animate-spin" />
                  {t('agentDetail.checkingReferences')}
                </div>
              ) : deleteFileRefs && deleteFileRefs.reference_count > 0 ? (
                <>
                  <p className="text-accent-400 font-medium mb-2">
                    {t('agentDetail.fileReferencedBy').replace('{count}', String(deleteFileRefs.reference_count))}
                  </p>
                  <div className="space-y-1 max-h-32 overflow-y-auto">
                    {deleteFileRefs.references.map((ref, i) => (
                      <div key={i} className="text-textMuted pl-2 border-l-2 border-accent-500/30">
                        {ref.display}
                      </div>
                    ))}
                  </div>
                  <p className="text-textMuted mt-2 text-3xs">{t('agentDetail.deleteFileConsequence')}</p>
                </>
              ) : (
                <p className="text-textMuted">{t('agentDetail.fileNoReferences')}</p>
              )}
            </div>

            <div className="flex gap-3">
              <button
                onClick={() => { setDeleteFileTarget(null); setDeleteFileRefs(null) }}
                className="flex-1 py-2.5 text-sm border border-border text-textSecondary rounded-card hover:bg-elevated font-medium transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleDeleteFile}
                disabled={deletingFile}
                className="flex-1 py-2.5 text-sm bg-rose-500 text-white rounded-card hover:bg-rose-400 font-medium transition-all disabled:opacity-50"
              >
                {deletingFile ? <Loader2 size={16} className="animate-spin mx-auto" /> : t('common.delete')}
              </button>
            </div>
          </div>
        </Dialog>
      )}

      {/* 完整设置弹窗 */}
      {agent && (
        <AgentSettingsModal
          agent={agent}
          modelOptions={modelOptions}
          providers={providersList}
          defaults={defaultModels}
          thinkingSupported={thinkingSupported}
          isOpen={showFullSettings}
          onClose={() => setShowFullSettings(false)}
          onSaved={() => loadAgent()}
        />
      )}
    </div>
  )
}
