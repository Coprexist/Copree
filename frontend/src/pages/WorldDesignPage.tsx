/**
 * 群视界设计页 — 世界文件管理 + 界面预览 + 群视界机器人对话
 *
 * 布局参考 TRAE/Cursor：左（文件树/预览）右（对话窗口）
 * 普通用户可直接用；专业用户可编辑代码（专业模式）。
 */
import { useEffect, useLayoutEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight, Folder, FolderOpen, FolderInput, Upload, Plus, Pencil, Eye, MessageCircle, Save, MoreHorizontal, FileText, Trash2, Settings, BookOpen, X, Download, Maximize2, Minimize2, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { api } from '../api/client'
import { saveText } from '../utils/download'
import GroupManagerModal from '../components/GroupManagerModal'
import WorldChatPanel, { type WorldChatHandle, type WorldSessionsSnapshot } from '../components/WorldChatPanel'
import MarkdownContent from '../components/shared/MarkdownContent'
import WorldFileTree, { buildWorldTree, type WorldFile } from '../components/world/WorldFileTree'
import WorldSessionList, { type WorldSessionInfo } from '../components/world/WorldSessionList'
import FileContentPane, { fileTypeIcon } from '../components/world/FileContentPane'
import WorldCreatorConfig, { type WorldCreator, type WorldUsageStats } from '../components/world/WorldCreatorConfig'
import WorldPreviewFrame, { WorldPreviewActions } from '../components/world/WorldPreviewPane'
import { getCodeLang, isMarkdownFile } from '../utils/mime'
import { useResizableSidebar } from '../hooks/useResizableSidebar'
import { useElementWidth } from '../hooks/useElementWidth'
import { Button, Dialog, IconButton, Input, MenuPanel, MenuItem } from '../components/ui'
import { useT } from '../i18n/I18nContext'

/**
 * 一次性的"进场"过渡：先落在未入场态，下一帧再翻到入场态。
 * 不用 keyframes 的原因——transition / duration / ease 那几套类能和面板其它动效共用同一套令牌，
 * 也不必往全局 CSS 里塞动画；放进 layout effect 是因为这次翻转必须发生在浏览器绘制之前，
 * 否则新内容会先整块闪一下再被藏起来。
 */
function useEnterTransition(deps: React.DependencyList) {
  const [entered, setEntered] = useState(true)
  useLayoutEffect(() => {
    setEntered(false)
    const id = requestAnimationFrame(() => setEntered(true))
    return () => cancelAnimationFrame(id)
  }, deps)
  return entered
}

/** 左栏页签：只有文字与颜色，下划线由页签行里那条**共享**指示器负责（见 railTabRefs） */
function RailTab({ active, label, badge, onClick, buttonRef }: {
  active: boolean
  label: string
  badge?: number
  onClick: () => void
  /** 回调 ref：共享指示器要量它的 offsetLeft/offsetWidth 才能滑过去 */
  buttonRef?: (el: HTMLButtonElement | null) => void
}) {
  return (
    <button
      ref={buttonRef}
      onClick={onClick}
      className={`h-full inline-flex items-center gap-1 text-xs transition-colors ${active ? 'text-primary-400 font-medium' : 'text-textMuted hover:text-textSecondary'}`}
    >
      {label}
      {!!badge && badge > 0 && (
        <span className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-rose-500 text-white text-3xs font-bold">
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  )
}

interface World {
  id: number
  name: string
  description: string
  owner_id: number
  status: string
  time_flow_rate: number
  world_time: string | null
  bindings: { entity_type: string; entity_id: number }[]
  agents: { agent_id: number; role: string }[]
  // 群视界机器人 = 世界配置（非 agent、无账号），身份 = world-{id}
  creator: WorldCreator | null
  /** 运行模式：auto | review | plan（后端 world_ai_mode 权威，只由用户/API 改） */
  ai_mode?: string
}

export default function WorldDesignPage() {
  const { worldId } = useParams()
  const navigate = useNavigate()
  const wid = Number(worldId)
  const t = useT()

  // 专注模式（?focus=1）：收起中栏（编辑/预览），把宽度让给对话；左栏与会话列表照旧在，
  // 所以专注时也还能切会话、翻文件。放 URL 而不是组件 state —— 刷新/前进后退都能还原，
  // 且 Layout 能据此一并收起应用侧边栏
  const [searchParams, setSearchParams] = useSearchParams()
  const chatFocus = searchParams.get('focus') === '1'
  const toggleChatFocus = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (chatFocus) next.delete('focus')
      else next.set('focus', '1')
      return next
    }, { replace: true })
  }, [chatFocus, setSearchParams])

  // 可拖拽面板（复用侧边栏 hook：左=左栏[会话/工作区]，右=对话）
  const fileTreeRef = useRef<HTMLDivElement>(null)
  const chatPanelRef = useRef<HTMLDivElement>(null)
  // 三栏动态保底：左栏 200（会话名要看得见）/ 编辑区 160 / 对话 360（对话栏要塞得下输入提示与建议卡）；
  // 上限按其他区域保底实时反推（防负：空间不足时至少 = 自身保底）
  const MIN_RAIL = 200
  const RAIL_MINI_W = 44  // 折叠后的图标条宽度（与内部的 w-11 对齐，别让容器比图标条宽）
  const MIN_EDITOR = 160
  const MIN_CHAT = 360
  const HANDLES = 8  // 两个拖拽手柄
  // 上限必须按「设计区实际宽度」反推：外层还有 Copree 导航栏占位，用 window.innerWidth 会多算约 240px，
  // 结果是文件树能拖到把对话栏挤出可视区。容器首帧未测量时退回 innerWidth。
  const [containerRef, containerWidth] = useElementWidth()
  const available = containerWidth || window.innerWidth
  // 沿用旧的存储键：用户拖过的宽度不因为"文件树改叫左栏"就丢
  const { sidebarWidth: fileWidth, handleResizeStart: fileResizeStart } = useResizableSidebar('world_files_width', fileTreeRef, {
    min: MIN_RAIL, max: () => Math.max(MIN_RAIL, available - MIN_EDITOR - MIN_CHAT - HANDLES),
  })
  // 聊天栏上限按「当前左栏实际宽度」实时反推（不是保底值）：左栏拖宽后，聊天栏同样不会被挤出右侧。
  // 专注模式下中栏（编辑/预览）不占位，上限把 MIN_EDITOR 让出来——否则专注时白留 160px 拖不过去
  const chatMax = () => Math.max(MIN_CHAT, available - fileWidth - (chatFocus ? 0 : MIN_EDITOR) - HANDLES)
  const { sidebarWidth: chatWidth, handleResizeStart: chatResizeStart } = useResizableSidebar('world_chat_width', chatPanelRef, {
    side: 'right', min: MIN_CHAT, max: chatMax,
  })
  // 渲染层兜底：无论拖拽/hook 状态怎么变，聊天栏实际宽度绝不超过可用空间（否则会被挤出可视区）
  const effectiveChatWidth = Math.min(chatWidth, chatMax())

  const [world, setWorld] = useState<World | null>(null)
  const [files, setFiles] = useState<WorldFile[]>([])
  const [currentFile, setCurrentFile] = useState<string>('')
  const currentFileRef = useRef('')  // load 闭包读实时值（避免 useCallback 冻结导致每次 load 都跳第一个文件）
  currentFileRef.current = currentFile
  const [content, setContent] = useState('')
  const [mode, setMode] = useState<'files' | 'preview'>('files')
  // ── 左栏（会话 / 工作区两个页签，常驻且可拖可折） ──
  const [railTab, setRailTab] = useState<'chat' | 'files'>('chat')
  // 折叠态记在本地：DSH 那种"折成图标条"是个人习惯，不该每次进页面都重来
  const [railCollapsed, setRailCollapsed] = useState(() => localStorage.getItem('world_rail_collapsed') === '1')
  useEffect(() => { localStorage.setItem('world_rail_collapsed', railCollapsed ? '1' : '0') }, [railCollapsed])
  // 拖宽过程中关掉宽度过渡：否则手柄跟手会慢半拍
  const [railDragging, setRailDragging] = useState(false)
  useEffect(() => {
    if (!railDragging) return
    const up = () => setRailDragging(false)
    window.addEventListener('mouseup', up)
    return () => window.removeEventListener('mouseup', up)
  }, [railDragging])
  const [topMenuOpen, setTopMenuOpen] = useState(false)
  // 会话快照与改名弹窗：数据与动作都在 WorldChatPanel 里，这里只存展示用的一份
  const [sessions, setSessions] = useState<WorldSessionInfo[]>([])
  const [currentSession, setCurrentSession] = useState('default')
  const [renaming, setRenaming] = useState<{ id: string } | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [previewKey, setPreviewKey] = useState(0)
  // 专注模式下的预览覆盖层：中栏被收起后，看预览只要一次点击（走覆盖层而不是展开中栏，
  // 这样对话列的宽度与滚动位置都不动）
  const [previewOpen, setPreviewOpen] = useState(false)
  // 退出专注时收掉覆盖层：否则回普通模式后它和中栏的预览会同时存在，两个"预览"打架
  useEffect(() => { if (!chatFocus) setPreviewOpen(false) }, [chatFocus])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  // 接口文档查看（程序员用：发给世界 AI 的 md）
  const [docsOpen, setDocsOpen] = useState(false)
  const [docsSections, setDocsSections] = useState<{ id: string; title: string; intro: string }[]>([])
  const [docsActive, setDocsActive] = useState<string>('')
  const [docsContent, setDocsContent] = useState('')
  const [docsLoading, setDocsLoading] = useState(false)
  const [docxAvailable, setDocxAvailable] = useState(false)
  const [isAdminUser, setIsAdminUser] = useState(false)
  const [downloadTarget, setDownloadTarget] = useState<{ scope: 'section' | 'all'; title: string } | null>(null)
  const openDocs = async () => {
    setDocsOpen(true)
    try {
      const r = await api.get<{ sections: { id: string; title: string; intro: string }[] }>('/kb')
      setDocsSections(r.sections || [])
      if (r.sections?.length) selectDoc(r.sections[0].id)
    } catch { /* ignore */ }
    try {
      const st = await api.get<{ docx_available: boolean; is_admin: boolean }>('/kb/status')
      setDocxAvailable(!!st.docx_available)
      setIsAdminUser(!!st.is_admin)
    } catch { setDocxAvailable(false); setIsAdminUser(false) }
  }
  const selectDoc = async (id: string) => {
    setDocsActive(id)
    setDocsLoading(true)
    try {
      const r = await api.get<{ content: string }>(`/kb/${id}`)
      setDocsContent(r.content || '')
    } catch { setDocsContent('（文档读取失败）') } finally { setDocsLoading(false) }
  }
  // 下载文档（md 文件）：本地内容落盘走 utils/download（唯一入口）
  const downloadDoc = (content: string, filename: string) => {
    saveText(content, filename, 'text/markdown;charset=utf-8')
  }
  // 收集全部文档 md（合并）
  const getAllDocsMd = async () => {
    const parts = ['# Copree 世界 API 接口文档\n']
    for (const sec of docsSections) {
      try {
        const r = await api.get<{ content: string }>(`/kb/${sec.id}`)
        parts.push(`\n\n---\n\n# ${sec.id} ${sec.title}\n\n` + (r.content || ''))
      } catch { /* 单区失败跳过 */ }
    }
    return parts.join('\n')
  }
  // 下载 docx（pandoc，POST 原生 fetch）
  const downloadDocx = async (md: string, filename: string) => {
    try {
      // docx 转换：POST 拿二进制（download 支持 POST），取回+落盘走唯一入口
      await api.download('/kb/convert', filename.endsWith('.docx') ? filename : filename + '.docx',
                         { method: 'POST', body: { md, filename } })
    } catch (e: any) { setMsg(`docx 导出失败: ${e?.message || e}`) }
  }
  // 下载弹窗确认：按格式执行
  const doDownload = async (format: 'md' | 'docx') => {
    if (!downloadTarget) return
    const { scope, title } = downloadTarget
    const filename = scope === 'section' ? `api-doc-${docsActive || 'doc'}` : 'copree-world-api-docs'
    if (format === 'md') {
      const md = scope === 'section' ? docsContent : await getAllDocsMd()
      downloadDoc(md, filename + '.md')
    } else {
      const md = scope === 'section' ? docsContent : await getAllDocsMd()
      await downloadDocx(md, filename + '.docx')
    }
    setDownloadTarget(null)
  }

  // 世界 AI 配置表单（单独表单，不属于 agent）
  const [showCreatorForm, setShowCreatorForm] = useState(false)
  // 2.7：LLM 用量/缓存命中率
  const [usageStats, setUsageStats] = useState<WorldUsageStats | null>(null)

  // 当前文件内联渲染：md 渲染 + 查看原文；html/代码高亮渲染；图片直接显示（不用弹窗）
  const [viewMode, setViewMode] = useState<'edit' | 'render'>('edit')

  // ── 视口响应式：移动端 / 桌面端条件渲染（避免双实例导致输入卡顿） ──
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 1024)
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 1024)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // ── 移动端（<lg）：tab 切换（文件/对话，对话默认打开）+ 目录逐层导航 ──
  const [mobileTab, setMobileTab] = useState<'files' | 'chat' | 'preview'>('chat')
  const [mobileView, setMobileView] = useState<'dirs' | 'file'>('dirs')
  const [mobileDir, setMobileDir] = useState('')  // 当前浏览目录（'' = 根）
  // 上传：顶部菜单（上传到此位置 / 选择其它位置）+ 目录选择弹层
  const [uploadMenuOpen, setUploadMenuOpen] = useState(false)
  const [uploadDirPickerOpen, setUploadDirPickerOpen] = useState(false)
  const [groupManagerOpen, setGroupManagerOpen] = useState(false)
  const [myId, setMyId] = useState<number | null>(null)

  useEffect(() => {
    try {
      const me = localStorage.getItem('user_info')
      if (me) setMyId(JSON.parse(me).id ?? null)
    } catch { /* ignore */ }
  }, [])
  const [uploadNavDir, setUploadNavDir] = useState('')
  const mobileUploadDirRef = useRef<string | null>(null)  // 非 null = 移动端上传，目标目录由此指定
  const fileExt = currentFile?.split('.').pop()?.toLowerCase() ?? ''
  const isMdFile = isMarkdownFile(currentFile ?? '', '')
  const fileCodeLang = currentFile ? getCodeLang(currentFile, '') : ''
  const isImgFile = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp'].includes(fileExt)
  const canRender = !!(isMdFile || fileCodeLang || isImgFile)

  // ── 加载世界 + 文件树 ──
  const load = useCallback(async () => {
    try {
      const w = await api.get<World>(`/worlds/${wid}`)
      setWorld(w)
      const f = await api.get<{ files: WorldFile[] }>(`/worlds/${wid}/files`)
      setFiles(f.files || [])
      // 2.7：缓存命中统计（失败静默，不影响主流程）
      try {
        const u = await api.get<WorldUsageStats>(`/worlds/${wid}/usage`)
        setUsageStats(u)
      } catch { /* ignore */ }
      // 默认选中第一个文件（仅当前确实没选中时；用 ref 读实时值，AI 工具刷新不会强制跳转）
      if (f.files?.length && !currentFileRef.current) {
        selectFile(f.files[0].path)
      }
    } catch (e: any) {
      setMsg(`加载失败: ${e?.message || e}`)
    } finally {
      setLoading(false)
    }
  }, [wid])

  // load 只依赖 wid：它内部调的 selectFile 也只在 wid 变化时重建，
  // 不能把 selectFile 写进 deps —— 那个声明在后面，deps 数组在渲染期求值会踩 TDZ
  useEffect(() => { load() }, [load])

  // ── 文件操作 ──
  // 传给 memo 过的文件树：identity 必须稳定，否则 memo 白做（只依赖 wid，换世界自然重建）
  const selectFile = useCallback(async (path: string) => {
    setCurrentFile(path)
    // 能内联渲染的文件默认渲染视图（md/html/代码/图片），其余默认编辑
    const ext = path.split('.').pop()?.toLowerCase() ?? ''
    const imgLike = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp'].includes(ext)
    setViewMode((isMarkdownFile(path, '') || getCodeLang(path, '') || imgLike) ? 'render' : 'edit')
    try {
      const r = await api.get<{ content: string | null; binary: boolean }>(
        `/worlds/${wid}/files/content?path=${encodeURIComponent(path)}`,
      )
      setContent(r.binary ? '(二进制文件，不可编辑)' : (r.content || ''))
    } catch { setContent('') }
  }, [wid])

  // ── 记录懒通知（改动/报错都走同一条通道，agent 下次对话时收到） ──
  const pushNotice = async (file: string, location: string, summary: string) => {
    if (!world?.creator) return   // 群视界机器人是世界的默认 AI，收件人不用指定
    try {
      await api.post(`/worlds/${wid}/notices`, { file, location, summary })
    } catch { /* 通知失败不影响主流程 */ }
  }

  const saveFile = async () => {
    if (!currentFile) return
    setSaving(true)
    try {
      await api.put(`/worlds/${wid}/files`, { path: currentFile, content })
      await pushNotice(currentFile, 'manual-edit', `用户手动编辑了 ${currentFile}`)
      setPreviewKey((k) => k + 1) // 刷新预览
      setMsg('已保存')
    } catch (e: any) {
      const errMsg = `保存失败: ${e?.message || e}`
      // 报错也进懒通知，agent 下次对话能看到
      await pushNotice(currentFile || 'unknown', 'save-error', errMsg)
      setMsg(errMsg)
    } finally {
      setSaving(false)
    }
  }

  const createFile = async () => {
    const name = prompt('新文件名（如 about.html）：')
    if (!name) return
    try {
      await api.put(`/worlds/${wid}/files`, { path: name, content: '' })
      await load()
      selectFile(name)
    } catch (e: any) {
      setMsg(`创建失败: ${e?.message || e}`)
    }
  }

  // ── 上传（先选择目标位置，再选文件） ──
  const fileInputRef = useRef<HTMLInputElement>(null)
  const uploadFile = async (f: File, targetPath: string) => {
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('path', targetPath.replace(/^\/+/, ''))
      await api.post(`/worlds/${wid}/files/upload`, fd)
      await load()
      selectFile(targetPath.replace(/^\/+/, ''))
      setMsg('已上传')
    } catch (err: any) {
      setMsg(`上传失败: ${err?.message || err}`)
    }
  }
  const handleUploadPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ''  // 允许重复选同一文件
    if (!f) return
    // 移动端：目标目录由 mobileUploadDirRef 指定（上传菜单/目录选择器设置）
    const mobileOverride = mobileUploadDirRef.current
    mobileUploadDirRef.current = null
    if (mobileOverride !== null) {
      await uploadFile(f, mobileOverride ? `${mobileOverride}/${f.name}` : f.name)
      setMobileView('file')  // 上传完自动打开文件（移动端）
      return
    }
    // 桌面端：prompt 输入目标路径（默认当前选中文件所在目录）
    const dir = currentFile?.includes('/') ? currentFile.slice(0, currentFile.lastIndexOf('/') + 1) : ''
    const target = prompt(`上传到哪个路径？（当前目录：${dir || '/'}）`, dir + f.name)
    if (!target) return
    await uploadFile(f, target)
  }
  // 移动端上传：菜单（上传到此位置 / 选择其它位置）
  const mobileUploadHere = () => {
    setUploadMenuOpen(false)
    mobileUploadDirRef.current = mobileDir
    fileInputRef.current?.click()
  }
  const mobileUploadElsewhere = () => {
    setUploadMenuOpen(false)
    setUploadNavDir('')
    setUploadDirPickerOpen(true)
  }
  const mobileUploadToNavDir = () => {
    setUploadDirPickerOpen(false)
    mobileUploadDirRef.current = uploadNavDir
    fileInputRef.current?.click()
  }

  // ── 删除（AI 侧已有 file_delete 工具，这里补前端入口） ──
  const deleteFile = useCallback(async (path: string) => {
    if (!confirm(`删除 ${path}？`)) return
    try {
      await api.delete(`/worlds/${wid}/files?path=${encodeURIComponent(path)}`)
      if (currentFile === path) setCurrentFile('')
      await load()
      setMsg(`已删除 ${path}`)
    } catch (e: any) {
      setMsg(`删除失败: ${e?.message || e}`)
    }
    // currentFile 进了依赖：删掉的正好是当前文件时要清掉选中
  }, [wid, currentFile, load])

  // ── 文件树（按目录层级构建，文件夹可折叠） ──
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set())

  const fileTree = useMemo(() => buildWorldTree(files), [files])

  // 移动端：当前浏览目录节点 / 上传目录选择器节点（从 fileTree 定位）
  const mobileDirNode = useMemo(() => {
    if (!mobileDir) return fileTree
    const parts = mobileDir.split('/')
    let node = fileTree
    for (const p of parts) {
      const next = node.children.find((c) => c.isDir && c.name === p)
      if (!next) break
      node = next
    }
    return node
  }, [fileTree, mobileDir])

  const uploadDirNode = useMemo(() => {
    if (!uploadNavDir) return fileTree
    const parts = uploadNavDir.split('/')
    let node = fileTree
    for (const p of parts) {
      const next = node.children.find((c) => c.isDir && c.name === p)
      if (!next) break
      node = next
    }
    return node
  }, [fileTree, uploadNavDir])

  // 折叠目录：identity 稳定，交给 memo 过的文件树
  const toggleDir = useCallback((path: string) => {
    setCollapsedDirs((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])

  // 发布到商城：跳转统一发布页（带当前世界预选，表单含标题/描述/标签/同步 GitHub）
  // ── 世界打包：下载 / 导入 zip ──
  const [worldZipOpen, setWorldZipOpen] = useState(false)
  const [worldImportOpen, setWorldImportOpen] = useState(false)
  const downloadWorldZip = async (includeContent: boolean) => {
    setWorldZipOpen(false)
    try {
      await api.download(`/worlds/${worldId}/export?include_content=${includeContent}`, `world_${worldId}.zip`)
      setMsg(includeContent ? '已下载世界包（含数据文件）' : '已下载世界包（不含数据文件）')
    } catch (err: any) { setMsg(`下载失败: ${err?.message || err}`) }
  }
  const importZipRef = useRef<HTMLInputElement>(null)
  const [importMode, setImportMode] = useState<'safe' | 'full'>('safe')
  const handleImportZip = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ''  // 允许重复选同一文件
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.zip')) { setMsg('请选择 zip 文件'); return }
    setWorldImportOpen(false)
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('exclude_content', String(importMode === 'safe'))
      const r = await api.post<{ imported: number; skipped_content?: number }>(`/worlds/${worldId}/files/import`, fd)
      setMsg(`导入成功：${r?.imported ?? 0} 个文件${r?.skipped_content ? `（跳过数据文件 ${r.skipped_content}）` : ''}`)
      load()
    } catch (err: any) { setMsg(`导入失败: ${err?.message || err}`) }
  }

  const publishToMarket = () => {
    if (!world) return
    navigate(`/market/publish?world_id=${wid}`)
  }

  // ── 聊天面板引用（内部管理所有聊天状态） ──
  const chatHandleRef = useRef<WorldChatHandle>(null)
  const [chatUnreadCount, setChatUnreadCount] = useState(0)

  // ── 左栏动效：下划线滑动 / 列表进场 / 折叠过渡 ──
  // 放在 chatUnreadCount 之后：量下划线要读未读数（徽标会改变页签宽度），
  // 放前面会踩"声明前使用"（依赖数组在渲染期求值，不是等执行到才读）
  const railTabRefs = useRef<Record<'chat' | 'files', HTMLButtonElement | null>>({ chat: null, files: null })
  const [tabIndicator, setTabIndicator] = useState<{ left: number; width: number } | null>(null)
  const railContentIn = useEnterTransition([railCollapsed])
  const railListIn = useEnterTransition([railTab])
  // will-change 只在指示器真的在滑的那 200ms 里挂着：常驻会占显存、还逼着合成层常驻
  const [tabSliding, setTabSliding] = useState(false)
  const tabSlideTimer = useRef<number | null>(null)
  const switchRailTab = useCallback((next: 'chat' | 'files') => {
    if (next === railTab) return
    setRailTab(next)
    setTabSliding(true)
    if (tabSlideTimer.current) window.clearTimeout(tabSlideTimer.current)
    // 过渡 duration-200，留一点余量再撤 will-change
    tabSlideTimer.current = window.setTimeout(() => setTabSliding(false), 260)
  }, [railTab])
  useEffect(() => () => { if (tabSlideTimer.current) window.clearTimeout(tabSlideTimer.current) }, [])

  /** 量当前页签在页签行里的位置：共享指示器靠它平移过去。
   *  量而不是写死百分比——文案宽度会变（未读徽标出现/消失，以后换词也会变） */
  const measureRailTab = useCallback(() => {
    const el = railTabRefs.current[railTab]
    if (!el) return
    const next = { left: el.offsetLeft, width: el.offsetWidth }
    // 值没变就交回同一个对象：否则每量一次都触发一轮渲染
    setTabIndicator((prev) => (prev && prev.left === next.left && prev.width === next.width ? prev : next))
  }, [railTab])
  useEffect(() => { measureRailTab() }, [measureRailTab, chatUnreadCount, railCollapsed])
  useEffect(() => {
    // 窗口变宽变窄、字号变化都会动到页签宽度，进来重算一次
    window.addEventListener('resize', measureRailTab)
    return () => window.removeEventListener('resize', measureRailTab)
  }, [measureRailTab])

  // 会话快照来自面板里那个 useWorldChat：值没变时引用不变，setState 会被 Object.is 吃掉，
  // 不会因为"上报"多渲染一轮
  const handleSessionsChange = useCallback((snapshot: WorldSessionsSnapshot) => {
    setSessions(snapshot.list)
    setCurrentSession(snapshot.current)
  }, [])

  // 左栏会话列表的回调：全部定住引用（只依赖 ref），否则会话行 memo 全废
  const handleSessionSelect = useCallback((id: string) => { chatHandleRef.current?.switchSession(id) }, [])
  const handleSessionNew = useCallback(() => { chatHandleRef.current?.newSession() }, [])
  const handleSessionTogglePin = useCallback(() => { chatHandleRef.current?.togglePinCurrent() }, [])
  const handleSessionRename = useCallback((id: string, title: string) => {
    setRenaming({ id })
    setRenameValue(title)
  }, [])
  const handleSessionExport = useCallback((id: string, fmt: 'md' | 'json', title: string) => {
    chatHandleRef.current?.exportSession(id, fmt, title || undefined)
  }, [])
  // 运行模式回调：面板是 memo 的，这里给内联箭头会每次重渲都换 identity，把 memo 废掉
  const handleModeChange = useCallback((mode: string) => {
    setWorld((w) => (w ? { ...w, ai_mode: mode } : w))
  }, [])

  /** 改名提交：留空 = 清除命名（列表回落到会话编号）；清洗规则在后端一处 */
  const submitRename = useCallback(async () => {
    if (!renaming) return
    await chatHandleRef.current?.renameSession(renaming.id, renameValue)
    setRenaming(null)
  }, [renaming, renameValue])

  // world 首次加载完成 → 聊天面板渲染 → 确保在底部（2026-08-13 修复：只在首次滚——
  // 之前依赖 [world]，工具 done 后 onRefresh→load→setWorld 每次都触发，
  // 把用户从任意位置无条件拉到底部）
  const worldLoadedRef = useRef(false)
  useEffect(() => {
    if (world && !worldLoadedRef.current) {
      worldLoadedRef.current = true
      chatHandleRef.current?.forceScrollToBottom()
    }
  }, [world])

  // ── 聊天面板内容（桌面右栏 / 移动端对话 tab 共用） ──
  // showFocus：桌面才有"专注"这个概念，移动端没有中栏可收，传 false 不留死按钮
  const renderChatInner = (showFocus: boolean) => {
    if (!world) return null
    return (
    <>
      <div className="flex items-center gap-2 px-3 h-9 border-b border-border shrink-0">
        <MessageCircle size={14} className="text-textMuted shrink-0" />
        <span className="text-sm font-medium truncate">{world.creator?.name || '群视界机器人'}</span>
        {chatUnreadCount > 0 && (
          <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-rose-500 text-white text-3xs font-bold animate-pulse">
            {chatUnreadCount > 99 ? '99+' : chatUnreadCount}
          </span>
        )}
        <span className="hidden sm:inline text-3xs px-1.5 py-0.5 rounded bg-elevated text-textMuted">{world.creator?.id}</span>
        <div className="flex-1" />
        {showFocus && (
          <button
            onClick={toggleChatFocus}
            className="icon-btn-sm shrink-0"
            title={chatFocus ? t('tool:world.chat.focus.off') : t('tool:world.chat.focus.on')}
            aria-label={chatFocus ? t('tool:world.chat.focus.off') : t('tool:world.chat.focus.on')}
          >
            {chatFocus ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        )}
      </div>

      {showCreatorForm && world && world.creator && (
        <WorldCreatorConfig
          wid={wid}
          creator={world.creator}
          usageStats={usageStats}
          aiMode={world.ai_mode || 'review'}
          onModeSaved={(mode) => setWorld((w) => (w ? { ...w, ai_mode: mode } : w))}
          onSaved={(updated) => setWorld((w) => (w ? { ...w, creator: updated } : w))}
          onClose={() => setShowCreatorForm(false)}
          onMsg={setMsg}
        />
      )}

      <WorldChatPanel
        ref={chatHandleRef}
        wid={wid}
        onRefresh={load}
        onMsg={setMsg}
        onUnreadCountChange={setChatUnreadCount}
        creatorName={world?.creator?.name}
        aiMode={world?.ai_mode || 'review'}
        onModeChange={handleModeChange}
        onSessionsChange={handleSessionsChange}
      />
    </>
    )
  }

  if (loading) return <div className="flex items-center justify-center h-screen text-textMuted">加载中...</div>
  if (!world) return <div className="p-8 text-textMuted">世界不存在</div>

  return (
    <div className="h-full bg-canvas text-textPrimary">
      {/* ═══ 移动端（<lg）：tab 切换 ── 文件（目录导航/编辑器）+ 对话（默认打开） ═══ */}
      {isMobile && <div className="flex flex-col h-full relative">
        {/* 顶栏：返回 + 世界信息 + 上传（文件 tab 时显示） */}
        <div className="flex items-center gap-2 px-3 py-2 bg-surface border-b border-border">
          <button onClick={() => navigate('/worlds')} className="inline-flex items-center gap-1 text-sm text-textMuted hover:text-textPrimary transition-colors shrink-0">
            <ChevronLeft size={14} />
            世界
          </button>
          <span className="font-semibold truncate">{world.name}</span>
          <span className={`hidden sm:inline-flex text-xs px-2 py-0.5 rounded-full shrink-0 ${world.status === 'active' ? 'bg-mint-500/20 text-mint-400' : 'bg-elevated text-textMuted'}`}>
            {world.status === 'active' ? '活跃' : '休眠'}
          </span>
          <div className="flex-1" />
          <button
            onClick={() => setShowCreatorForm((v) => !v)}
            className={`shrink-0 p-1.5 transition-colors ${showCreatorForm ? 'text-primary-400' : 'text-textMuted hover:text-textPrimary'}`}
            title="世界 AI 配置（单独表单，不属于 agent）"
          >
            <Settings size={14} />
          </button>
          <button onClick={openDocs} className="shrink-0 p-1.5 text-textMuted hover:text-textPrimary transition-colors" title="接口文档（发给世界 AI 的 md）">
            <BookOpen size={14} />
          </button>
          <button onClick={() => setWorldZipOpen(true)} className="shrink-0 p-1.5 text-textMuted hover:text-textPrimary transition-colors" title="下载世界包（zip）">
            <Download size={14} />
          </button>
          <button onClick={() => setWorldImportOpen(true)} className="shrink-0 p-1.5 text-textMuted hover:text-textPrimary transition-colors" title="导入世界包（zip 批量导入，不动数据文件）">
            <FolderInput size={14} />
          </button>
          <button onClick={publishToMarket} className="shrink-0 inline-flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors px-2 py-1 whitespace-nowrap" title="发布到世界商城">
            <Upload size={13} /> 发布
          </button>
          <button onClick={() => setGroupManagerOpen(true)} className="shrink-0 p-1.5 text-textMuted hover:text-textPrimary transition-colors" title="群类型与群助手">
            <MoreHorizontal size={16} />
          </button>
          {mobileTab === 'files' && (
            <div className="relative shrink-0">
              <button onClick={() => setUploadMenuOpen((v) => !v)} className="inline-flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors px-2 py-1 whitespace-nowrap" title="上传文件">
                <Upload size={14} />
                上传
              </button>
              {uploadMenuOpen && (
                <MenuPanel className="absolute right-0 top-full mt-1 w-56 p-1 z-modal">
                  <MenuItem onClick={mobileUploadHere} className="inline-flex items-center gap-1.5 text-xs">
                    <Upload size={13} /> {t('tool:world.files.uploadHere', { dir: mobileDir || '/' })}
                  </MenuItem>
                  <MenuItem onClick={mobileUploadElsewhere} className="inline-flex items-center gap-1.5 text-xs">
                    <FolderOpen size={13} /> {t('tool:world.files.uploadElsewhere')}
                  </MenuItem>
                </MenuPanel>
              )}
            </div>
          )}
        </div>

        {/* tab：文件 / 对话 / 预览（对话默认打开） */}
        <div className="flex items-stretch bg-surface border-b border-border">
          <button
            onClick={() => setMobileTab('files')}
            className={`flex-1 inline-flex items-center justify-center gap-1.5 py-2 text-sm transition-colors ${mobileTab === 'files' ? 'text-primary-400 border-b-2 border-primary-500 font-medium' : 'text-textMuted'}`}
          >
            <Folder size={14} /> 文件
          </button>
          <button
            onClick={() => setMobileTab('chat')}
            className={`flex-1 inline-flex items-center justify-center gap-1.5 py-2 text-sm transition-colors ${mobileTab === 'chat' ? 'text-primary-400 border-b-2 border-primary-500 font-medium' : 'text-textMuted'}`}
          >
            <MessageCircle size={14} /> 对话
          </button>
          <button
            onClick={() => setMobileTab('preview')}
            className={`flex-1 inline-flex items-center justify-center gap-1.5 py-2 text-sm transition-colors ${mobileTab === 'preview' ? 'text-primary-400 border-b-2 border-primary-500 font-medium' : 'text-textMuted'}`}
          >
            <Eye size={14} /> 预览
          </button>
        </div>

        {/* 内容区：对话 tab（默认）/ 预览 tab（iframe）/ 文件 tab（目录导航 → 编辑器） */}
        {mobileTab === 'chat' ? (
          <div className="flex-1 flex flex-col min-h-0 bg-surface">
            {renderChatInner(false)}
          </div>
        ) : mobileTab === 'preview' ? (
          <div className="flex-1 flex flex-col min-h-0">
            <div className="px-3 py-1.5 text-xs text-textSecondary bg-surface/60 border-b border-border flex items-center gap-2">
              <span className="truncate flex-1">{t('tool:world.pane.previewTitle', { url: `/world/${wid}/preview` })}</span>
              <WorldPreviewActions wid={wid} onRefresh={() => setPreviewKey((k) => k + 1)} />
            </div>
            <div className="flex-1 min-h-0">
              <WorldPreviewFrame wid={wid} previewKey={previewKey} />
            </div>
          </div>
        ) : mobileView === 'file' ? (
          <div className="flex-1 flex flex-col min-h-0">
            <div className="px-3 py-1.5 text-xs text-textSecondary bg-surface/60 border-b border-border flex items-center gap-2">
              <button onClick={() => setMobileView('dirs')} className="inline-flex items-center gap-0.5 text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors shrink-0">
                <ChevronLeft size={13} />
                返回
              </button>
              <span className="truncate flex-1">{currentFile || '未选择文件'}</span>
              {canRender && (
                <button
                  onClick={() => setViewMode((v) => (v === 'render' ? 'edit' : 'render'))}
                  className="text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors shrink-0"
                  title={viewMode === 'render' ? '切到原文/编辑' : '切到渲染视图'}
                >
                  {viewMode === 'render' ? (isMdFile ? '查看原文' : '编辑') : '渲染'}
                </button>
              )}
              {viewMode !== 'render' && (
                <button onClick={saveFile} disabled={saving} className="text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors shrink-0">
                  {saving ? '保存中...' : (<span className="inline-flex items-center gap-1"><Save size={12} /> 保存</span>)}
                </button>
              )}
            </div>
            <FileContentPane wid={wid} currentFile={currentFile} content={content} setContent={setContent} viewMode={viewMode} canRender={canRender} isMdFile={isMdFile} fileCodeLang={fileCodeLang} isImgFile={isImgFile} />
          </div>
        ) : (
          <div className="flex-1 flex flex-col min-h-0">
            {/* 面包屑（路径可逐级返回） */}
            <div className="flex items-center gap-0.5 px-3 py-2 bg-surface/60 border-b border-border overflow-x-auto text-xs">
              <button onClick={() => setMobileDir('')} className="text-primary-400 hover:underline shrink-0">/</button>
              {mobileDir.split('/').filter(Boolean).map((seg, i, arr) => {
                const path = mobileDir.split('/').slice(0, i + 1).join('/')
                const isLast = i === arr.length - 1
                return isLast ? (
                  <span key={path} className="flex items-center gap-0.5 min-w-0">
                    <ChevronRight size={11} className="text-textMuted shrink-0" />
                    <span className="text-textSecondary truncate">{seg}</span>
                  </span>
                ) : (
                  <span key={path} className="flex items-center gap-0.5 shrink-0">
                    <ChevronRight size={11} className="text-textMuted" />
                    <button onClick={() => setMobileDir(path)} className="text-primary-400 hover:underline">{seg}</button>
                  </span>
                )
              })}
            </div>
            {/* 目录内容：文件夹在前，点文件夹进入；点文件打开编辑器 */}
            <div className="flex-1 overflow-y-auto p-2">
              {mobileDirNode.children.length === 0 && (
                <div className="text-xs text-textMuted text-center mt-10 px-4">
                  空目录<br />点右上角「上传」放文件，或去「对话」让机器人生成
                </div>
              )}
              {mobileDirNode.children.map((n) => n.isDir ? (
                <button
                  key={n.path}
                  onClick={() => setMobileDir(n.path)}
                  className="w-full flex items-center gap-2 px-2 py-2.5 rounded-control hover:bg-elevated text-textSecondary text-sm transition-colors"
                >
                  <Folder size={16} className="text-primary-400 shrink-0" />
                  <span className="truncate flex-1 text-left">{n.name}</span>
                  <ChevronRight size={14} className="text-textMuted shrink-0" />
                </button>
              ) : (
                <div key={n.path} className="flex items-center">
                  <button
                    onClick={() => { selectFile(n.path); setMobileView('file') }}
                    className={`flex items-center gap-2 flex-1 min-w-0 text-left px-2 py-2.5 rounded-control text-sm transition-colors ${currentFile === n.path ? 'bg-primary-500/20 text-primary-300' : 'hover:bg-elevated text-textSecondary'}`}
                  >
                    <span className="shrink-0">{fileTypeIcon(n.name)}</span>
                    <span className="truncate flex-1">{n.name}</span>
                  </button>
                  <button
                    onClick={() => deleteFile(n.path)}
                    className="shrink-0 w-8 h-8 flex items-center justify-center text-textMuted hover:text-rose-400 transition-colors"
                    title="删除此文件"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 目录选择弹层（上传 → 选择其它位置） */}
        {uploadDirPickerOpen && (
          <div className="absolute inset-0 z-modal bg-black/50 flex items-end">
            <div className="w-full bg-surface rounded-t-2xl border-t border-border flex flex-col max-h-[75%]">
              <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
                <span className="text-sm font-semibold flex-1">选择上传位置</span>
                <button onClick={() => setUploadDirPickerOpen(false)} className="text-xs text-textMuted hover:text-textPrimary px-2 py-1">取消</button>
              </div>
              <div className="flex items-center gap-0.5 px-4 py-2 overflow-x-auto text-xs border-b border-border/60">
                <button onClick={() => setUploadNavDir('')} className="text-primary-400 hover:underline shrink-0">/</button>
                {uploadNavDir.split('/').filter(Boolean).map((seg, i, arr) => {
                  const path = uploadNavDir.split('/').slice(0, i + 1).join('/')
                  const isLast = i === arr.length - 1
                  return isLast ? (
                    <span key={path} className="flex items-center gap-0.5 min-w-0">
                      <ChevronRight size={11} className="text-textMuted shrink-0" />
                      <span className="text-textSecondary truncate">{seg}</span>
                    </span>
                  ) : (
                    <span key={path} className="flex items-center gap-0.5 shrink-0">
                      <ChevronRight size={11} className="text-textMuted" />
                      <button onClick={() => setUploadNavDir(path)} className="text-primary-400 hover:underline">{seg}</button>
                    </span>
                  )
                })}
              </div>
              <div className="flex-1 overflow-y-auto p-2">
                {uploadDirNode.children.filter((n) => n.isDir).length === 0 && (
                  <div className="text-xs text-textMuted text-center mt-8">当前目录没有子文件夹</div>
                )}
                {uploadDirNode.children.filter((n) => n.isDir).map((n) => (
                  <button
                    key={n.path}
                    onClick={() => setUploadNavDir(n.path)}
                    className="w-full flex items-center gap-2 px-3 py-2.5 rounded-control hover:bg-elevated text-textSecondary text-sm transition-colors"
                  >
                    <Folder size={16} className="text-primary-400 shrink-0" />
                    <span className="truncate flex-1 text-left">{n.name}</span>
                    <ChevronRight size={14} className="text-textMuted shrink-0" />
                  </button>
                ))}
              </div>
              <div className="p-3 border-t border-border">
                <button onClick={mobileUploadToNavDir} className="btn btn-md btn-primary w-full">
                  上传到此位置（{uploadNavDir || '/'}）
                </button>
              </div>
            </div>
          </div>
        )}
      </div>}

      {/* ═══ 桌面端（≥lg）：标题栏 + 三栏（分隔线贯穿，拖拽手柄覆盖标题栏与内容区） ═══ */}
      {!isMobile && <div ref={containerRef} className="flex flex-col h-full">
        {/* 世界标题栏：只留一行。原来"文件/预览 + 接口文档 + 下载/导入/发布"摊在标题里，
            窄窗口下世界名会被挤没；全收进 ⋯ 后标题栏从两行降到一行 */}
        <div className="flex items-center gap-2 px-3 h-12 bg-surface border-b border-border shrink-0">
          <button onClick={() => navigate('/worlds')} className="icon-btn-sm shrink-0" title={t('tool:world.top.back')} aria-label={t('tool:world.top.back')}>
            <ChevronLeft size={16} />
          </button>
          <span className="font-semibold truncate" title={world.name}>{world.name}</span>
          <span className={`shrink-0 text-xs px-2 py-0.5 rounded-full ${world.status === 'active' ? 'bg-mint-500/20 text-mint-400' : 'bg-elevated text-textMuted'}`}>
            {world.status === 'active' ? t('tool:world.top.status.active') : t('tool:world.top.status.hibernating')}
          </span>
          <span className="hidden xl:inline shrink-0 text-xs text-textMuted">{t('tool:world.top.flow', { rate: String(world.time_flow_rate) })}</span>
          <div className="flex-1" />
          {msg && <span className="min-w-0 truncate text-xs text-accent-400">{msg}</span>}
          <input ref={importZipRef} type="file" accept=".zip" className="hidden" onChange={handleImportZip} />
          {/* 专注模式下中栏（含 文件/预览 页签）被收起，这里留一个一步可达的预览入口；
              普通模式仍用中栏的页签，不重复放第二个开关 */}
          {chatFocus && (
            <button
              onClick={() => setPreviewOpen(true)}
              className="shrink-0 inline-flex items-center gap-1 h-7 px-2 rounded-control text-xs bg-elevated hover:bg-border text-textSecondary transition-colors"
              title={t('tool:world.preview.open')}
            >
              <Eye size={12} /> {t('tool:world.pane.preview')}
            </button>
          )}
          <button
            onClick={() => setShowCreatorForm((v) => !v)}
            className={`shrink-0 inline-flex items-center gap-1 h-7 px-2 rounded-control text-xs transition-colors ${showCreatorForm ? 'bg-primary-500/15 text-primary-400' : 'bg-elevated hover:bg-border text-textSecondary'}`}
            title="世界 AI 配置（单独表单，不属于 agent）"
          >
            <Settings size={12} /> {t('tool:world.top.config')}
          </button>
          <div className="relative shrink-0">
            <button onClick={() => setTopMenuOpen((v) => !v)} className="icon-btn-sm" title={t('tool:world.top.more')} aria-label={t('tool:world.top.more')}>
              <MoreHorizontal size={16} />
            </button>
            {topMenuOpen && (
              <>
                {/* 透明遮罩收起：与页面里其它小菜单同一套做法，不挂 document 监听 */}
                <div className="fixed inset-0 z-modal" onClick={() => setTopMenuOpen(false)} />
                <MenuPanel className="absolute right-0 top-full mt-1 w-52 py-1 z-toast">
                  {([
                    { key: 'docs', icon: <BookOpen size={13} />, label: t('tool:world.top.docs'), run: openDocs },
                    { key: 'export', icon: <Download size={13} />, label: t('tool:world.top.export'), run: () => setWorldZipOpen(true) },
                    { key: 'import', icon: <FolderInput size={13} />, label: t('tool:world.top.import'), run: () => setWorldImportOpen(true) },
                    { key: 'publish', icon: <Upload size={13} />, label: t('tool:world.top.publish'), run: publishToMarket },
                    { key: 'groups', icon: <MessageCircle size={13} />, label: t('tool:world.top.groups'), run: () => setGroupManagerOpen(true) },
                  ]).map((it) => (
                    <MenuItem
                      key={it.key}
                      onClick={() => { setTopMenuOpen(false); it.run() }}
                      className="inline-flex items-center gap-2"
                    >
                      {it.icon} {it.label}
                    </MenuItem>
                  ))}
                </MenuPanel>
              </>
            )}
          </div>
        </div>
        {/* 内容行：左栏（会话/工作区，常驻可拖可折） + 中栏（编辑/预览，专注模式收起） + 右栏（对话） */}
        <div className="flex flex-1 min-h-0">
          {/* 左栏：宽度**不做过渡**——壳每变一次宽，兄弟列（编辑区/专注模式下的对话列）都要重新布局，
              所以折叠只让壳跳一下，动效全压在内容层的 opacity/transform 上（合成层，零布局）。
              内层固定按"展开宽度"排版，宽度方向靠壳裁切，避免动画期间文字逐帧回流。
              没加 contain:layout_paint：它会把左栏变成 fixed 后代的包含块并裁掉它们，
              左栏里的"点外面关菜单"遮罩就会只剩左栏那么大（那个遮罩已经改成 document 监听） */}
          <div
            ref={fileTreeRef}
            className="flex flex-col shrink-0 bg-surface border-r border-border overflow-hidden"
            style={{ width: railCollapsed ? RAIL_MINI_W : fileWidth }}
          >
            <div
              className={`flex flex-col flex-1 min-h-0 shrink-0 transition-[opacity,transform] duration-150 ease-out motion-reduce:transition-none ${railContentIn ? 'opacity-100 translate-x-0' : 'opacity-0 -translate-x-1'}`}
              style={railCollapsed ? undefined : { width: fileWidth }}
            >
              {railCollapsed ? (
                /* 折成图标条（学 DSH）：点图标＝展开并切到那一栏，图标条本身不承载列表 */
                <div className="w-11 flex flex-col items-center gap-1 py-2">
                  <button onClick={() => setRailCollapsed(false)} className="icon-btn-sm" title={t('tool:world.rail.expand')} aria-label={t('tool:world.rail.expand')}>
                    <PanelLeftOpen size={15} />
                  </button>
                  <button
                    onClick={() => { setRailTab('chat'); setRailCollapsed(false) }}
                    className={`icon-btn-sm ${railTab === 'chat' ? 'text-primary-400' : ''}`}
                    title={t('tool:world.rail.tab.chat')}
                    aria-label={t('tool:world.rail.tab.chat')}
                  >
                    <MessageCircle size={15} />
                  </button>
                  <button
                    onClick={() => { setRailTab('files'); setRailCollapsed(false) }}
                    className={`icon-btn-sm ${railTab === 'files' ? 'text-primary-400' : ''}`}
                    title={t('tool:world.rail.tab.files')}
                    aria-label={t('tool:world.rail.tab.files')}
                  >
                    <Folder size={15} />
                  </button>
                  {chatUnreadCount > 0 && (
                    <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-rose-500 text-white text-3xs font-bold">
                      {chatUnreadCount > 99 ? '99+' : chatUnreadCount}
                    </span>
                  )}
                </div>
              ) : (
                <>
                  {/* 页签行：下划线是行内一条**共享的**指示器，靠 translateX 滑到当前页签
                      （各自淡入淡出会闪）；行必须是 relative——offsetLeft 就是相对它量的 */}
                  <div className="relative flex items-center gap-3 h-9 px-2 border-b border-border shrink-0">
                    <RailTab
                      active={railTab === 'chat'}
                      label={t('tool:world.rail.tab.chat')}
                      badge={chatUnreadCount}
                      onClick={() => switchRailTab('chat')}
                      buttonRef={(el) => { railTabRefs.current.chat = el }}
                    />
                    <RailTab
                      active={railTab === 'files'}
                      label={t('tool:world.rail.tab.files')}
                      onClick={() => switchRailTab('files')}
                      buttonRef={(el) => { railTabRefs.current.files = el }}
                    />
                    <div className="flex-1" />
                    <button onClick={() => setRailCollapsed(true)} className="icon-btn-sm" title={t('tool:world.rail.collapse')} aria-label={t('tool:world.rail.collapse')}>
                      <PanelLeftClose size={15} />
                    </button>
                    {/* 指示器只动 transform：宽度走 scaleX（基准 1px），连"改宽度"都不做，
                        滑动与宽度变化就都留在合成层；will-change 只在滑的那 200ms 挂着，滑完撤掉 */}
                    {tabIndicator && (
                      <span
                        aria-hidden
                        className={`pointer-events-none absolute -bottom-px left-0 h-0.5 w-px origin-left bg-primary-500 transition-transform duration-200 ease-out motion-reduce:transition-none ${tabSliding ? 'will-change-transform' : ''}`}
                        style={{ transform: `translateX(${tabIndicator.left}px) scaleX(${tabIndicator.width})` }}
                      />
                    )}
                  </div>
                  <div className="flex-1 min-h-0 overflow-y-auto">
                    {/* 切页签：新列表淡入 + 上移 3px（150ms ease-out），只为止住"啪一下换掉"的突兀 */}
                    <div className={`transition-[opacity,transform] duration-150 ease-out motion-reduce:transition-none ${railListIn ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-1'}`}>
                      {railTab === 'chat' ? (
                        <WorldSessionList
                          sessions={sessions}
                          current={currentSession}
                          onSelect={handleSessionSelect}
                          onNew={handleSessionNew}
                          onRename={handleSessionRename}
                          onTogglePin={handleSessionTogglePin}
                          onExport={handleSessionExport}
                        />
                      ) : (
                        <div className="p-2">
                          <div className="flex items-center justify-between mb-2 px-1">
                            <span className="text-xs font-medium text-textSecondary">{t('tool:world.pane.files')}</span>
                            <span className="flex items-center gap-2">
                              <button onClick={() => fileInputRef.current?.click()} className="inline-flex items-center gap-0.5 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors" title={t('tool:world.files.upload')}>
                                <Upload size={12} /> {t('tool:world.files.upload')}
                              </button>
                              <button onClick={createFile} className="inline-flex items-center gap-0.5 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors" title={t('tool:world.files.new')}>
                                <Plus size={12} /> {t('tool:world.files.new')}
                              </button>
                            </span>
                          </div>
                          <input ref={fileInputRef} type="file" className="hidden" onChange={handleUploadPick} />
                          {files.length === 0 && <div className="text-xs text-textMuted p-2">{t('tool:world.files.empty')}</div>}
                          <WorldFileTree files={files} currentFile={currentFile} collapsedDirs={collapsedDirs} onToggleDir={toggleDir} onSelect={selectFile} onDelete={deleteFile} />
                        </div>
                      )}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
          {!railCollapsed && (
            <div
              onMouseDown={(e) => { setRailDragging(true); fileResizeStart(e) }}
              className="w-1 shrink-0 cursor-col-resize hover:bg-primary-500/40 transition-colors relative z-overlay"
            />
          )}

          {/* 中栏：编辑 / 预览。专注模式整块让给对话——文件内容与预览状态在 store/iframe 里，重新挂载不丢 */}
          {!chatFocus && (
            <div className="flex-1 flex flex-col min-w-0" style={{ minWidth: MIN_EDITOR }}>
              <div className="flex items-center gap-2 h-9 px-2 bg-surface border-b border-border shrink-0">
                <div className="flex items-center gap-0.5 p-0.5 rounded-control bg-elevated border border-border shrink-0">
                  <button onClick={() => setMode('files')} className={`px-2 py-0.5 text-2xs rounded-control transition-colors ${mode === 'files' ? 'bg-surface text-textPrimary font-medium shadow-sm' : 'text-textMuted hover:text-textPrimary'}`}>{t('tool:world.pane.files')}</button>
                  <button onClick={() => setMode('preview')} className={`px-2 py-0.5 text-2xs rounded-control transition-colors ${mode === 'preview' ? 'bg-surface text-textPrimary font-medium shadow-sm' : 'text-textMuted hover:text-textPrimary'}`}>{t('tool:world.pane.preview')}</button>
                </div>
                <span className="min-w-0 truncate text-xs text-textSecondary">
                  {mode === 'preview' ? t('tool:world.pane.previewTitle', { url: `/world/${wid}/preview` }) : (currentFile || t('tool:world.pane.noSelection'))}
                </span>
                <div className="flex-1" />
                {mode === 'files' ? (currentFile && (
                  <span className="flex items-center gap-3 shrink-0 pr-1">
                    {canRender && (
                      <button
                        onClick={() => setViewMode((v) => (v === 'render' ? 'edit' : 'render'))}
                        className="inline-flex items-center gap-0.5 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors"
                        title={viewMode === 'render' ? '切到原文/编辑' : '切到渲染视图'}
                      >
                        {viewMode === 'render' ? (isMdFile ? (
                          <><FileText size={12} /> {t('tool:world.pane.viewSource')}</>
                        ) : (
                          <><Pencil size={12} /> {t('tool:world.pane.edit')}</>
                        )) : (
                          <><Eye size={12} /> {t('tool:world.pane.render')}</>
                        )}
                      </button>
                    )}
                    {viewMode !== 'render' && (
                      <button onClick={saveFile} disabled={saving} className="inline-flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors">
                        <Save size={12} /> {saving ? t('tool:world.pane.saving') : t('tool:world.pane.save')}
                      </button>
                    )}
                  </span>
                )) : (
                  <WorldPreviewActions wid={wid} onRefresh={() => setPreviewKey((k) => k + 1)} />
                )}
              </div>
              <div className="flex-1 min-h-0">
                {mode === 'files' ? (
                  <div className="h-full flex flex-col">
                    <FileContentPane wid={wid} currentFile={currentFile} content={content} setContent={setContent} viewMode={viewMode} canRender={canRender} isMdFile={isMdFile} fileCodeLang={fileCodeLang} isImgFile={isImgFile} />
                  </div>
                ) : (
                  <WorldPreviewFrame wid={wid} previewKey={previewKey} />
                )}
              </div>
            </div>
          )}

          {!chatFocus && (
            <div onMouseDown={chatResizeStart} className="w-1 shrink-0 cursor-col-resize hover:bg-primary-500/40 transition-colors relative z-overlay" />
          )}

          {/* 右栏：对话（常驻，切页签不动它）。普通模式＝可拖的固定宽度；专注模式＝flex-1 吃掉中栏让出的空间 */}
          <div
            ref={chatPanelRef}
            className={`flex flex-col bg-surface border-l border-border ${chatFocus ? 'flex-1 min-w-0' : 'shrink-0'}`}
            style={chatFocus ? undefined : { width: effectiveChatWidth, maxWidth: effectiveChatWidth }}
          >
            {/* 限宽与居中已经挪进对话面板（按可拖的"内容列宽"走，拖拽零重渲），
                这里只把整列交给对话——专注时列自然变宽，内容仍居中 */}
            <div className="flex-1 min-h-0 flex flex-col">
              {renderChatInner(true)}
            </div>
          </div>
        </div>
      </div>}

      {/* 接口文档查看（程序员用）——手机全屏，桌面居中双栏 */}
      {docsOpen && (
        <Dialog onClose={() =>  setDocsOpen(false)} layer="toast" className="flex md:items-center justify-center">
          <div className="w-full md:max-w-4xl bg-surface md:border md:border-border md:rounded-dialog md:border-b-0 h-full md:h-auto md:max-h-[85vh] flex flex-col shadow-xl overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
              <div className="flex items-center gap-2 min-w-0">
                <BookOpen size={15} className="text-primary-400 shrink-0" />
                <span className="text-sm font-semibold text-textPrimary truncate">世界 API 接口文档</span>
                <span className="text-3xs text-textMuted hidden sm:inline">发给世界 AI 的 md（view_api_doc 同源）</span>
              </div>
              <button onClick={() => setDocsOpen(false)} className="p-1 text-textMuted hover:text-textPrimary transition-colors shrink-0" title="关闭"><X size={16} /></button>
            </div>
            <div className="flex flex-1 min-h-0 flex-col md:flex-row">
              {/* 分区列表：手机顶部横向滚动条，桌面左侧栏 */}
              <div className="shrink-0 md:w-52 md:border-r md:border-border overflow-x-auto md:overflow-y-auto p-2 space-y-1 flex md:flex-col gap-1">
                {docsSections.map((sec) => (
                  <button
                    key={sec.id}
                    onClick={() => selectDoc(sec.id)}
                    className={`md:w-full text-left px-2.5 py-2 rounded-control transition-colors shrink-0 md:shrink md:flex-1 ${docsActive === sec.id ? 'bg-primary-500/15 text-primary-300' : 'hover:bg-elevated text-textSecondary'}`}
                  >
                    <div className="text-xs font-medium whitespace-nowrap md:whitespace-normal">{sec.id} {sec.title}</div>
                    <div className="text-3xs text-textMuted line-clamp-2 mt-0.5 hidden md:block">{sec.intro}</div>
                  </button>
                ))}
              </div>
              {/* 内容 */}
              <div className="flex-1 flex flex-col min-w-0 min-h-0">
                <div className="flex items-center gap-2 px-4 py-2 border-b border-border shrink-0 flex-wrap">
                  <span className="text-xs text-textSecondary truncate flex-1 min-w-0">
                    {docsSections.find((s) => s.id === docsActive)?.title || '接口文档'}
                  </span>
                  <button
                    onClick={() => setDownloadTarget({ scope: 'section', title: '下载此分区' })}
                    disabled={!docsContent || docsLoading}
                    className="btn btn-sm btn-secondary shrink-0"
                  >
                    <Download size={12} /> 下载此分区
                  </button>
                  <button
                    onClick={() => setDownloadTarget({ scope: 'all', title: '下载全部' })}
                    className="btn btn-sm btn-secondary shrink-0"
                  >
                    <Download size={12} /> 下载全部
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto p-4 min-w-0">
                {docsLoading ? (
                  <div className="flex items-center justify-center py-16 text-textMuted text-sm">加载中…</div>
                ) : (
                  <div className="max-w-none prose prose-sm dark:prose-invert [&_pre]:!bg-transparent [&_pre]:!p-0 [&_pre]:!m-0 [&_pre]:!rounded-none [&_pre]:!border-0">
                    <MarkdownContent content={docsContent} isMine={false} />
                  </div>
                )}
                </div>
              </div>
            </div>
          </div>
        </Dialog>
      )}

      {/* 下载类型弹窗（md / docx）——手机底部抽屉，桌面居中 */}
      {downloadTarget && (
        <div className="fixed inset-0 z-toast bg-black/60 flex items-end md:items-center justify-center" onClick={() => setDownloadTarget(null)}>
          <div className="w-full md:max-w-xs bg-surface border-t md:border border-border md:rounded-dialog rounded-none p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-textPrimary mb-1">{downloadTarget.title}</div>
            <div className="text-3xs text-textMuted mb-3">
              {downloadTarget.scope === 'section' ? `当前分区：${docsSections.find((s) => s.id === docsActive)?.title || docsActive || '文档'}` : '全部分区合并'}
            </div>
            <div className="space-y-2">
              <button
                onClick={() => doDownload('md')}
                className="btn btn-md btn-secondary w-full"
              >
                <FileText size={13} /> 下载 .md
              </button>
              {docxAvailable && (
                <button
                  onClick={() => doDownload('docx')}
                  className="btn btn-md btn-primary w-full"
                >
                  <FileText size={13} /> 下载 .docx（Word）
                </button>
              )}
            </div>
            {!docxAvailable && isAdminUser && (
              <div className="mt-3 text-3xs text-accent-400/90 leading-relaxed">
                如需下载为 docx（Word），请前往管理页安装 pandoc 插件后重启后端。
              </div>
            )}
            <button onClick={() => setDownloadTarget(null)} className="btn btn-md btn-ghost w-full mt-2">取消</button>
          </div>
        </div>
      )}

      {/* 世界包下载（含/不含数据文件） */}
      {worldZipOpen && (
        <Dialog onClose={() =>  setWorldZipOpen(false)} layer="toast" className="flex items-center justify-center p-4">
          <div className="w-full max-w-xs bg-surface border border-border rounded-dialog p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-1">
              <Download size={15} className="text-primary-400" />
              <span className="text-sm font-semibold text-textPrimary">下载世界包</span>
            </div>
            <div className="text-xs text-textMuted mb-4">导出为 zip 压缩包，Windows 可直接解压。</div>
            <div className="space-y-2">
              <button
                onClick={() => downloadWorldZip(true)}
                className="w-full text-left px-3.5 py-3 rounded-card border border-border bg-elevated/40 hover:bg-elevated transition-colors"
              >
                <div className="flex items-center gap-2 text-sm text-textPrimary"><Download size={13} className="text-primary-400" /> 完整备份（含数据文件）</div>
                <div className="text-3xs text-textMuted mt-1 pl-5">代码 + 资源 + 运行数据（content/）</div>
              </button>
              <button
                onClick={() => downloadWorldZip(false)}
                className="w-full text-left px-3.5 py-3 rounded-card border border-border bg-elevated/40 hover:bg-elevated transition-colors"
              >
                <div className="flex items-center gap-2 text-sm text-textPrimary"><Download size={13} className="text-primary-400" /> 仅代码与资源</div>
                <div className="text-3xs text-textMuted mt-1 pl-5">不含运行数据，适合分享给他人</div>
              </button>
            </div>
            <button onClick={() => setWorldZipOpen(false)} className="w-full mt-3 py-1.5 text-xs text-textMuted hover:text-textPrimary transition-colors">取消</button>
          </div>
        </Dialog>
      )}

      {/* 世界包导入（选择策略 → 选文件） */}
      {worldImportOpen && (
        <Dialog onClose={() =>  setWorldImportOpen(false)} layer="toast" className="flex items-center justify-center p-4">
          <div className="w-full max-w-xs bg-surface border border-border rounded-dialog p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-1">
              <FolderInput size={15} className="text-primary-400" />
              <span className="text-sm font-semibold text-textPrimary">导入世界包</span>
            </div>
            <div className="text-xs text-textMuted mb-4">从 zip 导入：已存在的同名文件会被替换，其余文件保持不变。</div>
            <div className="space-y-2">
              <button
                onClick={() => { setImportMode('safe'); importZipRef.current?.click() }}
                className="w-full text-left px-3.5 py-3 rounded-card border border-primary-500/40 bg-primary-500/10 hover:bg-primary-500/15 transition-colors"
              >
                <div className="flex items-center gap-2 text-sm text-textPrimary"><FolderInput size={13} className="text-primary-400" /> 保留数据文件</div>
                <div className="text-3xs text-textMuted mt-1 pl-5">只替换代码与资源，运行数据（content/）不动 · 推荐</div>
              </button>
              <button
                onClick={() => { setImportMode('full'); importZipRef.current?.click() }}
                className="w-full text-left px-3.5 py-3 rounded-card border border-border bg-elevated/40 hover:bg-elevated transition-colors"
              >
                <div className="flex items-center gap-2 text-sm text-textPrimary"><FolderInput size={13} className="text-primary-400" /> 连同数据文件替换</div>
                <div className="text-3xs text-textMuted mt-1 pl-5">代码、资源、运行数据全部按包内版本替换</div>
              </button>
            </div>
            <button onClick={() => setWorldImportOpen(false)} className="w-full mt-3 py-1.5 text-xs text-textMuted hover:text-textPrimary transition-colors">取消</button>
          </div>
        </Dialog>
      )}

      {/* 群类型与群助手管理（… 菜单） */}
      {groupManagerOpen && world && (
        <GroupManagerModal
          worldId={wid}
          isOwner={myId !== null && world.owner_id === myId}
          onClose={() => setGroupManagerOpen(false)}
        />
      )}

      {/* 专注模式的预览覆盖层：复用同一份预览实现与 z-modal 档浮层底座（Dialog 负责 ESC/点遮罩关闭）。
          覆盖层不改任何对话状态——面板只是被盖住，不会被卸载 */}
      {previewOpen && (
        <Dialog layer="modal" onClose={() => setPreviewOpen(false)} className="flex items-center justify-center p-4">
          <div
            className="w-full max-w-5xl h-[80vh] bg-surface border border-border rounded-dialog shadow-xl overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border shrink-0">
              <Eye size={15} className="text-primary-400 shrink-0" />
              <span className="text-sm font-semibold truncate min-w-0">
                {t('tool:world.pane.previewTitle', { url: `/world/${wid}/preview` })}
              </span>
              <div className="flex-1" />
              <WorldPreviewActions wid={wid} onRefresh={() => setPreviewKey((k) => k + 1)} />
              <IconButton size="sm" icon={<X size={16} />} label={t('common.close')} onClick={() => setPreviewOpen(false)} />
            </div>
            <div className="flex-1 min-h-0">
              <WorldPreviewFrame wid={wid} previewKey={previewKey} />
            </div>
          </div>
        </Dialog>
      )}

      {/* 会话改名弹窗（左栏会话列表的 ⋯ 里触发）：留空 = 清除命名，列表回落到会话编号 */}
      {renaming && (
        <Dialog className="flex items-center justify-center p-4" onClose={() => setRenaming(null)}>
          <div className="w-full max-w-sm bg-surface border border-border rounded-dialog shadow-xl p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-textPrimary">{t('tool:world.session.renameTitle')}</div>
            <Input
              autoFocus
              maxLength={20}
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submitRename() }}
              placeholder={t('tool:world.session.renamePlaceholder')}
              hint={t('tool:world.session.renameHint')}
            />
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="outline" onClick={() => setRenaming(null)}>{t('common.cancel')}</Button>
              <Button size="sm" onClick={submitRename}>{t('common.save')}</Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  )
}