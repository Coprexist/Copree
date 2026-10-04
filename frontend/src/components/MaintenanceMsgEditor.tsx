import { useState, useEffect, useRef, useCallback } from 'react'
import { api, getApiBaseUrl } from '../api/client'
import { Save, Upload, Pencil, Check, Trash2, X, Image as ImageIcon, AlertTriangle, Lock, Megaphone, Info } from 'lucide-react'
import { useT } from '../i18n/I18nContext'

interface MsgData {
  hard_title: string; hard_body: string; hard_color: string; hard_text_color: string
  hard_image: string; hard_style: string
  soft_text: string; soft_color: string; soft_text_color: string; soft_style: string; soft_once: boolean
}

interface PresetItem extends MsgData { name: string }

const DEFAULT_MSG: MsgData = {
  hard_title: '正在更新', hard_body: '服务器正在更新，稍等一下就好~',
  hard_color: '#f59e0b', hard_text_color: '#ffffff', hard_image: '', hard_style: 'popup',
  soft_text: '服务器正在调整，功能可能偶尔不稳定', soft_color: '#f59e0b', soft_text_color: '#ffffff', soft_style: 'banner', soft_once: false,
}

function toMsgData(d: any): MsgData {
  return {
    hard_title: d.hard_title ?? DEFAULT_MSG.hard_title,
    hard_body: d.hard_body ?? DEFAULT_MSG.hard_body,
    hard_color: d.hard_color ?? DEFAULT_MSG.hard_color,
    hard_text_color: d.hard_text_color ?? DEFAULT_MSG.hard_text_color,
    hard_image: d.hard_image ?? '',
    hard_style: d.hard_style ?? 'popup',
    soft_text: d.soft_text ?? DEFAULT_MSG.soft_text,
    soft_color: d.soft_color ?? DEFAULT_MSG.soft_color,
    soft_text_color: d.soft_text_color ?? DEFAULT_MSG.soft_text_color,
    soft_style: d.soft_style ?? 'banner',
    soft_once: !!d.soft_once,
  }
}

/** 把上传后的 file_id 拼成可访问的公开 URL（兼容桌面端实例地址） */
function publicFileUrl(fileId: number | string): string {
  return `${getApiBaseUrl()}/fs/public/${fileId}`
}

/** 从 URL 提取用于展示的文件名 */
function urlLabel(url: string): string {
  if (!url) return ''
  const m = url.match(/\/fs\/public\/(\d+)/)
  if (m) return `图片 #${m[1]}`
  try { return decodeURIComponent(url.split('/').pop() || url).slice(0, 24) } catch { return url }
}

type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'

/** 判断补丁属于硬维护还是软维护字段 */
function patchSection(patch: Partial<MsgData>): 'hard' | 'soft' | 'both' {
  const keys = Object.keys(patch) as (keyof MsgData)[]
  const hasHard = keys.some(k => k.startsWith('hard'))
  const hasSoft = keys.some(k => k.startsWith('soft'))
  if (hasHard && hasSoft) return 'both'
  if (hasHard) return 'hard'
  if (hasSoft) return 'soft'
  return 'both'
}

export default function MaintenanceMsgEditor() {
  const t = useT()
  const [msg, setMsg] = useState<MsgData>(DEFAULT_MSG)
  const [presets, setPresets] = useState<PresetItem[]>([])
  const [images, setImages] = useState<string[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [hardState, setHardState] = useState<SaveState>('idle')
  const [softState, setSoftState] = useState<SaveState>('idle')
  const [saveError, setSaveError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [selPreset, setSelPreset] = useState('')
  const [presetInput, setPresetInput] = useState('')   // 保存预设名称输入
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [presetError, setPresetError] = useState('')
  const [mtState, setMtState] = useState<{ hard: boolean; soft: boolean; auto: boolean } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = async () => {
    try {
      const [m, p, img] = await Promise.all([
        api.get('/admin/maintenance/msg'), api.get('/admin/maintenance/presets'), api.get('/admin/maintenance/images'),
      ])
      setMsg(toMsgData(m))
      setPresets((p as any).presets || [])
      setImages((img as any).images || [])
      setLoaded(true)
      setLoadError('')
    } catch (e: any) {
      setLoadError(e?.detail || e?.message || '加载维护配置失败')
    }
  }

  useEffect(() => {
    load()
    api.get('/admin/maintenance').then((d: any) => setMtState({ hard: !!d.hard, soft: !!d.soft, auto: !!d.auto })).catch(() => {})
  }, [])

  // 始终持有最新 msg，避免定时器/闭包读到旧值
  const msgRef = useRef(msg)
  msgRef.current = msg

  /** 保存文案到后端（全量提交，广播给在线用户）；section 决定哪个栏显示状态 */
  const save = useCallback(async (section: 'hard' | 'soft' | 'both') => {
    if (saveTimer.current) { clearTimeout(saveTimer.current); saveTimer.current = null }
    const setState = (s: SaveState) => {
      if (section === 'both' || section === 'hard') setHardState(s)
      if (section === 'both' || section === 'soft') setSoftState(s)
    }
    setState('saving'); setSaveError('')
    try {
      await api.put('/admin/maintenance/msg', msgRef.current)
      setState('saved')
      // 通知全局 Layout 立即刷新维护提示（改完马上看到，不等 30s 轮询）
      window.dispatchEvent(new CustomEvent('maintenance-saved'))
      setTimeout(() => {
        if (section === 'both' || section === 'hard') setHardState(s => (s === 'saved' ? 'idle' : s))
        if (section === 'both' || section === 'soft') setSoftState(s => (s === 'saved' ? 'idle' : s))
      }, 2500)
    } catch (e: any) {
      setState('error')
      setSaveError(e?.detail || e?.message || '保存失败，请重试')
    }
  }, [])

  /** 字段变更：标记对应栏未保存并自动保存（防抖 1.5s，全量提交） */
  const updateMsg = (patch: Partial<MsgData>) => {
    setMsg(prev => ({ ...prev, ...patch }))
    const sec = patchSection(patch)
    if (sec === 'hard' || sec === 'both') setHardState('dirty')
    if (sec === 'soft' || sec === 'both') setSoftState('dirty')
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => save(sec), 1500)
  }

  // 卸载时若还有未保存修改则立即保存
  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
  }, [])

  /** 每栏标题行右侧的保存按钮 */
  const SaveButton = ({ section, state, onSave }: { section: 'hard' | 'soft'; state: SaveState; onSave: () => void }) => {
    const label = state === 'dirty' ? '保存更改' : state === 'saving' ? '保存中…' : state === 'saved' ? '已保存' : '保存并更新'
    return (
      <button
        onClick={onSave}
        disabled={state === 'saving' || state === 'saved'}
        className={`flex items-center gap-1 px-2.5 py-1 rounded-control text-2xs font-medium transition-colors ${
          state === 'dirty'
            ? 'bg-primary-500 text-white hover:bg-primary-600'
            : state === 'saved'
              ? 'bg-mint-500/15 text-mint-400'
              : state === 'error'
                ? 'bg-rose-500/15 text-rose-400'
                : 'bg-canvas border border-border text-textSecondary hover:text-primary-400 hover:border-primary-500/40'
        }`}
      >
        {state === 'saved' ? <Check size={12} /> : <Save size={12} />}{label}
      </button>
    )
  }

  const applyPreset = async (name: string) => {
    setPresetError('')
    try {
      // 后端 apply 会合并全字段并写入 msg 文件 + 广播；前端同步本地状态
      const r: any = await api.post(`/admin/maintenance/presets/apply?name=${encodeURIComponent(name)}`)
      if (saveTimer.current) clearTimeout(saveTimer.current)
      setMsg(toMsgData(r.msg))
      setHardState('idle'); setSoftState('idle')
      setSaveError('')
      window.dispatchEvent(new CustomEvent('maintenance-saved'))
    } catch (e: any) {
      setPresetError(e?.detail || e?.message || '应用预设失败')
    }
  }

  const savePreset = async (name: string) => {
    const n = (name || '').trim()
    if (!n) { setPresetError('请输入预设名称'); return }
    setPresetError('')
    try {
      // 同名直接覆盖（后端 upsert），不再先删后建
      await api.post('/admin/maintenance/presets', { name: n, ...msg })
      setSelPreset(n)
      load()
    } catch (e: any) {
      setPresetError(e?.detail || e?.message || '保存预设失败')
    }
  }

  const renamePreset = async (oldName: string, newName: string) => {
    const nn = (newName || '').trim()
    if (!nn || nn === oldName) { setRenaming(null); return }
    const p = presets.find(x => x.name === oldName)
    if (!p) return
    setPresetError('')
    try {
      await api.delete(`/admin/maintenance/presets/${encodeURIComponent(oldName)}`)
      await api.post('/admin/maintenance/presets', { ...p, name: nn })
      if (selPreset === oldName) setSelPreset(nn)
      setRenaming(null)
      load()
    } catch (e: any) {
      setPresetError(e?.detail || e?.message || '重命名失败')
    }
  }

  const deletePreset = async (name: string) => {
    setPresetError('')
    try {
      await api.delete(`/admin/maintenance/presets/${encodeURIComponent(name)}`)
      if (selPreset === name) setSelPreset('')
      load()
    } catch (e: any) {
      setPresetError(e?.detail || e?.message || '删除失败')
    }
  }

  const handleUpload = async (file: File) => {
    if (uploading) return
    setUploading(true); setSaveError('')
    try {
      const r: any = await api.upload('/fs/upload-attachment', file)
      const url = publicFileUrl(r.file_id)
      updateMsg({ hard_image: url })
      try { await api.post(`/admin/maintenance/images?url=${encodeURIComponent(url)}`); setImages(prev => [url, ...prev.filter(x => x !== url)]) } catch {}
    } catch (e: any) {
      setSaveError(e?.detail || e?.message || '上传失败')
    }
    setUploading(false)
  }

  const addImageUrl = async () => {
    const url = prompt('输入外网图片URL：')
    if (!url) return
    try { await api.post(`/admin/maintenance/images?url=${encodeURIComponent(url)}`); setImages(prev => [url, ...prev.filter(x => x !== url)]); updateMsg({ hard_image: url }) } catch (e: any) { setSaveError(e?.detail || '添加图片失败') }
  }

  const removeImage = async (url: string) => {
    try {
      await api.delete(`/admin/maintenance/images?url=${encodeURIComponent(url)}`)
      setImages(prev => prev.filter(x => x !== url))
      if (msg.hard_image === url) updateMsg({ hard_image: '' })
    } catch (e: any) { setSaveError(e?.detail || '删除图片失败') }
  }

  if (!loaded) {
    return (
      <div className="bg-surface rounded-card border border-border p-4">
        {loadError ? (
          <div className="flex items-center gap-2 text-xs text-rose-400">
            <AlertTriangle size={14} /> {loadError}
            <button onClick={load} className="btn btn-xs btn-outline ml-auto hover:text-textPrimary">重试</button>
          </div>
        ) : (
          <p className="text-xs text-textMuted">{t('common:loading')}</p>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3">

      {/* ── 维护状态提示行（保存按钮在各栏内） ── */}
      <div className="flex flex-wrap items-center gap-2">
        {mtState && (
          <span className={`text-2xs px-2 py-0.5 rounded-full ${
            mtState.hard ? 'bg-rose-500/15 text-rose-400' : mtState.soft ? 'bg-accent-500/15 text-accent-400' : 'bg-mint-500/15 text-mint-400'
          }`}>
            {mtState.auto ? '启动中' : mtState.hard ? '暂停服务中' : mtState.soft ? '温馨提示中' : '未开启'}
          </span>
        )}
        {mtState && !mtState.hard && !mtState.soft && (
          <span className="flex items-center gap-1.5 text-2xs text-textMuted">
            <Info size={12} /> 保存的文案将在开启「暂停服务」或「温馨提示」后展示给用户
          </span>
        )}
        {saveError && <span className="text-2xs text-rose-400 flex items-center gap-1"><AlertTriangle size={12} />{saveError}</span>}
      </div>

      {/* ── 预设栏 ── */}
      <div className="bg-surface rounded-card border border-border p-3.5 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <select value={selPreset} onChange={e => { const v = e.target.value; if (v) applyPreset(v); else setSelPreset('') }}
            className="flex-1 min-w-[140px] px-3 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50">
            <option value="">{t('admin:presets')} ···</option>
            {presets.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
          </select>
          <div className="flex items-center gap-1.5">
            <input
              value={presetInput}
              placeholder={selPreset ? '另存为新预设…' : '新预设名称'}
              onChange={e => setPresetInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && presetInput.trim()) { savePreset(presetInput); setPresetInput('') } }}
              className="w-28 px-2 py-1.5 rounded-control border border-border bg-canvas text-2xs text-textPrimary focus:outline-none"
            />
            <button onClick={() => { if (presetInput.trim()) savePreset(presetInput); setPresetInput('') }}
              className="shrink-0 px-2.5 py-1.5 text-2xs rounded-control bg-primary-500/10 border border-primary-500/30 text-primary-400 hover:bg-primary-500/20 transition-colors">
              <Save size={13} className="inline mr-0.5" />存预设
            </button>
          </div>
          {selPreset && (
            <div className="flex items-center gap-1">
              <button onClick={() => { setRenaming(selPreset); setRenameValue(selPreset) }} className="p-1.5 rounded-control text-textMuted hover:text-textPrimary hover:bg-elevated transition-colors" title="重命名"><Pencil size={13} /></button>
              <button onClick={() => deletePreset(selPreset)} className="p-1.5 rounded-control text-rose-400/70 hover:text-rose-400 hover:bg-rose-500/10 transition-colors" title="删除"><Trash2 size={13} /></button>
            </div>
          )}
        </div>
        {renaming && (
          <div className="flex items-center gap-2">
            <input
              autoFocus
              value={renameValue}
              onChange={e => setRenameValue(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') renamePreset(renaming, renameValue); if (e.key === 'Escape') setRenaming(null) }}
              className="flex-1 px-2 py-1.5 rounded-control border border-border bg-canvas text-2xs focus:outline-none focus:ring-1 focus:ring-primary-500/50"
            />
            <button onClick={() => renamePreset(renaming, renameValue)}
              className="px-2 py-1.5 text-2xs rounded-control bg-mint-500/10 border border-mint-500/30 text-mint-400 hover:bg-mint-500/20"><Check size={12} /></button>
            <button onClick={() => setRenaming(null)} className="p-1.5 text-textMuted hover:text-textPrimary"><X size={13} /></button>
          </div>
        )}
        {presetError && <p className="text-2xs text-rose-400">{presetError}</p>}
      </div>

      {/* ── 双栏编辑 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* 硬维护 */}
        <div className="bg-surface rounded-card border border-border p-4 space-y-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-textPrimary">
            <span className="w-2 h-2 rounded-full bg-rose-400 animate-pulse" />
            <Lock size={13} className="text-rose-400" /> 暂停服务
            <span className="text-textMuted font-normal text-3xs ml-1 hidden sm:inline">——用户看到弹窗/顶栏，API 全部返回 503</span>
            <span className="ml-auto shrink-0">
              <SaveButton section="hard" state={hardState} onSave={() => save('hard')} />
            </span>
          </div>
          <div className="space-y-2.5">
            <div className="flex gap-2 items-start">
              <div className="relative shrink-0">
                <input type="color" value={msg.hard_color} onChange={e => updateMsg({ hard_color: e.target.value })}
                  onMouseDown={e => e.stopPropagation()} className="absolute inset-0 opacity-0 w-8 h-8 cursor-pointer" />
                <div className="w-8 h-8 rounded-control border border-border/50" style={{ backgroundColor: msg.hard_color }} />
              </div>
              <div className="flex-1 space-y-2">
                <input value={msg.hard_title} onChange={e => updateMsg({ hard_title: e.target.value })} placeholder="标题"
                  className="w-full px-3 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary focus:outline-none focus:border-primary-500/40" />
                <input value={msg.hard_body} onChange={e => updateMsg({ hard_body: e.target.value })} placeholder="正文"
                  className="w-full px-3 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary focus:outline-none focus:border-primary-500/40" />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <span className="text-3xs text-textMuted">文字色</span>
                <div className="relative">
                  <input type="color" value={msg.hard_text_color} onChange={e => updateMsg({ hard_text_color: e.target.value })}
                    onMouseDown={e => e.stopPropagation()} className="absolute inset-0 opacity-0 w-6 h-6 cursor-pointer" />
                  <div className="w-6 h-6 rounded border border-border/50" style={{ backgroundColor: msg.hard_text_color }} />
                </div>
              </div>
              <select value={msg.hard_style} onChange={e => updateMsg({ hard_style: e.target.value })}
                className="px-2.5 py-1.5 rounded-control border border-border bg-canvas text-2xs text-textPrimary focus:outline-none">
                <option value="popup">弹窗</option>
                <option value="banner">顶栏</option>
              </select>
            </div>
            <div>
              <div className="flex gap-1.5">
                <input value={msg.hard_image} onChange={e => updateMsg({ hard_image: e.target.value })} placeholder="图片 URL"
                  className="flex-1 px-3 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary focus:outline-none focus:border-primary-500/40" />
                <button onClick={addImageUrl} className="btn btn-xs btn-outline shrink-0 bg-canvas text-2xs hover:text-primary-400" title="添加外链"><ImageIcon size={13} /></button>
                <label className={`shrink-0 px-2.5 py-1.5 rounded-control border border-border bg-canvas text-2xs text-textSecondary hover:text-primary-400 cursor-pointer transition-colors ${uploading ? 'opacity-40 pointer-events-none' : ''}`}>
                  {uploading ? '···' : <Upload size={13} />}
                  <input ref={fileRef} type="file" accept="image/*" className="hidden" disabled={uploading} onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f); e.target.value = '' }} />
                </label>
              </div>
              {/* 图库缩略图 */}
              {images.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {images.map((url, i) => (
                    <div key={i} className={`group relative rounded-control overflow-hidden border transition-colors ${msg.hard_image === url ? 'border-primary-500 ring-1 ring-primary-500/40' : 'border-border/50 hover:border-primary-500/40'}`}>
                      <button onClick={() => updateMsg({ hard_image: url })} className="block w-12 h-12" title={urlLabel(url)}>
                        <img src={url} alt="" className="w-12 h-12 object-cover bg-black/20" />
                      </button>
                      <button onClick={() => removeImage(url)}
                        className="absolute top-0.5 right-0.5 hidden group-hover:flex w-4 h-4 items-center justify-center rounded bg-black/60 text-white"
                        title="删除"><X size={10} /></button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {/* 硬维护实时预览 */}
            <div className="rounded-control border border-border/30 overflow-hidden">
              <div className="text-3xs text-textMuted px-3 py-1 bg-canvas/50 border-b border-border/30">预览</div>
              <div className="p-2.5">
                {msg.hard_style === 'banner' ? (
                  <div className="text-2xs rounded-control px-3 py-2 flex items-center justify-center gap-2" style={{ backgroundColor: msg.hard_color, color: msg.hard_text_color }}>
                    <span>{msg.hard_title} · {msg.hard_body}</span>
                  </div>
                ) : (
                  <div className="rounded-card border border-border/40 bg-canvas/60 p-3 text-center">
                    {msg.hard_image && <img src={msg.hard_image} alt="" className="w-12 h-12 object-contain mx-auto mb-1.5 rounded" onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />}
                    <div className="text-xs font-semibold mb-1" style={{ color: msg.hard_color }}>{msg.hard_title}</div>
                    <div className="text-2xs text-textSecondary mb-2">{msg.hard_body}</div>
                    <span className="inline-block px-3 py-1 text-3xs rounded-control" style={{ backgroundColor: msg.hard_color, color: msg.hard_text_color }}>知道了</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* 软维护 */}
        <div className="bg-surface rounded-card border border-border p-4 space-y-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-textPrimary">
            <span className="w-2 h-2 rounded-full bg-accent-400" />
            <Megaphone size={13} className="text-accent-400" /> 温馨提示
            <span className="text-textMuted font-normal text-3xs ml-1 hidden sm:inline">——用户看到顶栏/弹窗提示，API 正常运行</span>
            <span className="ml-auto shrink-0">
              <SaveButton section="soft" state={softState} onSave={() => save('soft')} />
            </span>
          </div>
          <div className="space-y-2.5">
            <div className="flex gap-2 items-start">
              <div className="relative shrink-0">
                <input type="color" value={msg.soft_color} onChange={e => updateMsg({ soft_color: e.target.value })}
                  onMouseDown={e => e.stopPropagation()} className="absolute inset-0 opacity-0 w-8 h-8 cursor-pointer" />
                <div className="w-8 h-8 rounded-control border border-border/50" style={{ backgroundColor: msg.soft_color }} />
              </div>
              <input value={msg.soft_text} onChange={e => updateMsg({ soft_text: e.target.value })} placeholder="播报文字"
                className="flex-1 px-3 py-1.5 rounded-control border border-border bg-canvas text-xs text-textPrimary focus:outline-none focus:border-primary-500/40" />
            </div>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <span className="text-3xs text-textMuted">文字色</span>
                <div className="relative">
                  <input type="color" value={msg.soft_text_color} onChange={e => updateMsg({ soft_text_color: e.target.value })}
                    onMouseDown={e => e.stopPropagation()} className="absolute inset-0 opacity-0 w-6 h-6 cursor-pointer" />
                  <div className="w-6 h-6 rounded border border-border/50" style={{ backgroundColor: msg.soft_text_color }} />
                </div>
              </div>
              <select value={msg.soft_style} onChange={e => updateMsg({ soft_style: e.target.value })}
                className="px-2.5 py-1.5 rounded-control border border-border bg-canvas text-2xs text-textPrimary focus:outline-none">
                <option value="banner">顶栏</option>
                <option value="popup">弹窗</option>
              </select>
              <label className="flex items-center gap-1.5 text-2xs text-textSecondary cursor-pointer select-none ml-auto">
                <input type="checkbox" checked={msg.soft_once} onChange={e => updateMsg({ soft_once: e.target.checked })} className="rounded" />
                仅首次
              </label>
            </div>
            {msg.soft_text && (
              <div className="rounded-control border border-border/30 overflow-hidden">
                <div className="text-3xs text-textMuted px-3 py-1 bg-canvas/50 border-b border-border/30">预览</div>
                <div className="p-2.5">
                  {msg.soft_style === 'banner' ? (
                    <div className="text-2xs rounded-control px-3 py-2 text-center" style={{ backgroundColor: msg.soft_color, color: msg.soft_text_color }}>
                      {msg.soft_text}
                    </div>
                  ) : (
                    <div className="rounded-card border border-border/40 bg-canvas/60 p-3 text-center">
                      <div className="text-xs mb-2" style={{ color: msg.soft_text_color }}>{msg.soft_text}</div>
                      <span className="inline-block px-3 py-1 text-3xs rounded-control" style={{ backgroundColor: msg.soft_color, color: msg.soft_text_color }}>知道了</span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
