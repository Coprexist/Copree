/**
 * 从聊天记录导入 —— 创建 AI 的第三个入口。
 *
 * 三步：选来源（链接 / 上传文件 / 粘贴全文）→ 预览（多少条、什么平台、几个附件没有原文件）
 * → 带着这份对话去创建。解析在服务端（`/chat-import/resolve`），这一页只负责把
 * 「要导入什么」拿全，并让用户在真的落库之前看清楚。
 *
 * 链接这一格需要抓取插件：没装插件时后端回 409，这里直接把话说在前面，
 * 而不是等用户点了才报错。粘贴与上传永远可用——那是保底路径。
 */
import { useRef, useState } from 'react'
import { Link2, Upload, ClipboardPaste, AlertTriangle, ArrowLeft, FileText, Sparkles } from 'lucide-react'
import { api } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import { Button } from '../ui'
import ConfigFallbackDialog, { type NeedsConfig } from '../ConfigFallbackDialog'

export interface TranscriptStats {
  messages: number
  user_messages: number
  assistant_messages: number
  attachments: number
  missing_attachments: number
  first_at: string | null
  last_at: string | null
}

export interface Organized {
  prompt: string
  memories: Array<{ content: string; importance: number }>
  knowledge: Array<{ title: string; summary: string; content: string }>
}

export interface ResolvedImport {
  transcript: Record<string, unknown>
  stats: TranscriptStats
  warnings: string[]
  origin: string
  /** 「初步摘要」的正文：导入时写进 AI 的上下文账本，压缩后会被新摘要顶掉 */
  summary?: string | null
  /** 智能整理的结果：提示词预填创建表单，记忆/知识库随 apply 一起落库 */
  organized?: Organized | null
}

type SourceType = 'url' | 'text' | 'file'

export default function ChatImportPanel({ onReady, onBack }: {
  onReady: (resolved: ResolvedImport) => void
  onBack: () => void
}) {
  const t = useT()
  const [type, setType] = useState<SourceType>('url')
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [resolved, setResolved] = useState<ResolvedImport | null>(null)
  // 初步摘要：服务端给一句草稿，用户可改。空着就不注入（不写一条没内容的占位）
  const [summary, setSummary] = useState('')
  // ── 智能整理 ──
  const [organizing, setOrganizing] = useState(false)
  const [organized, setOrganized] = useState<Organized | null>(null)
  const [organizeError, setOrganizeError] = useState('')
  const [needsConfig, setNeedsConfig] = useState<NeedsConfig | null>(null)
  // 勾选状态按下标走：整理解出来的条目不多，用数组够了
  const [pickMemories, setPickMemories] = useState<Record<number, boolean>>({})
  const [pickKnowledge, setPickKnowledge] = useState<Record<number, boolean>>({})
  const fileRef = useRef<HTMLInputElement>(null)

  const pickFile = async (file: File | null) => {
    if (!file) return
    setError('')
    try {
      setText(await file.text())
      setFileName(file.name)
    } catch {
      setError(t('modal:importReadFailed'))
    }
  }

  const resolve = async () => {
    setBusy(true)
    setError('')
    try {
      // 上传文件在前端读成文本再提交：解析只看内容（JSON / Markdown / 纯文本都是文本），
      // 服务端不必为"文件"再开一条接收通道，文件名只用来给这段对话起标题
      const source = type === 'url'
        ? { type: 'url', value: url.trim() }
        : { type: 'text', value: text, filename: fileName || undefined }
      const res = await api.post<{ transcript: Record<string, unknown>; stats: TranscriptStats; warnings: string[]; suggested_summary?: string }>(
        '/chat-import/resolve', { source },
      )
      const origin = String((res.transcript as any)?.origin?.platform || '')
      setSummary(res.suggested_summary || '')
      setResolved({ ...res, origin })
    } catch (err: any) {
      setError(err?.message || t('modal:importFailed'))
    } finally {
      setBusy(false)
    }
  }

  /** 智能整理：把这段对话提炼成人设 + 记忆 + 知识库 + 初摘要。
   *  缺配置时不报错——后端回 needs_config，这里弹统一对话框（带「去设置默认配置」）。 */
  const runOrganize = async () => {
    if (!resolved) return
    setOrganizing(true)
    setOrganizeError('')
    try {
      const res = await api.post<{
        ok: boolean
        needs_config?: NeedsConfig
        prompt?: string
        memories?: Organized['memories']
        knowledge?: Organized['knowledge']
        summary?: string
      }>('/chat-import/organize', { transcript: resolved.transcript })

      if (!res.ok) {
        setNeedsConfig(res.needs_config || { reason: 'not_configured' })
        return
      }
      const next: Organized = {
        prompt: res.prompt || '',
        memories: res.memories || [],
        knowledge: res.knowledge || [],
      }
      setOrganized(next)
      setPickMemories(Object.fromEntries(next.memories.map((_, i) => [i, true])))
      setPickKnowledge(Object.fromEntries(next.knowledge.map((_, i) => [i, true])))
      if (res.summary) setSummary(res.summary)   // 整理出来的初摘要回填，用户还能改
    } catch (err: any) {
      setOrganizeError(err?.message || t('modal:organizeFailed'))
    } finally {
      setOrganizing(false)
    }
  }

  const canResolve = type === 'url' ? url.trim().length > 0 : text.trim().length > 0
  const tabs: Array<{ id: SourceType; icon: typeof Link2; label: string }> = [
    { id: 'url', icon: Link2, label: t('modal:importSourceUrl') },
    { id: 'file', icon: Upload, label: t('modal:importSourceFile') },
    { id: 'text', icon: ClipboardPaste, label: t('modal:importSourceText') },
  ]

  return (
    <div className="flex h-full flex-col bg-canvas">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[720px] px-4 py-5 md:px-6 md:py-6">
          <button onClick={onBack} className="mb-4 inline-flex items-center gap-1 text-xs text-textMuted hover:text-textSecondary">
            <ArrowLeft size={13} /> {t('modal:importBack')}
          </button>
          <h2 className="text-lg font-semibold text-textPrimary">{t('modal:importTitle')}</h2>
          <p className="mt-1 text-xs text-textSecondary">{t('modal:importHint')}</p>

          {/* 未解析前：选来源 */}
          {!resolved && (
            <>
              <div className="mt-4 flex gap-1.5">
                {tabs.map(({ id, icon: Icon, label }) => (
                  <Button
                    key={id}
                    size="sm"
                    variant={type === id ? 'tinted' : 'ghost'}
                    icon={<Icon size={13} />}
                    onClick={() => { setType(id); setError('') }}
                  >
                    {label}
                  </Button>
                ))}
              </div>

              <div className="mt-3">
                {type === 'url' && (
                  <div>
                    <input
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      placeholder="https://chat.deepseek.com/share/…"
                      className="field w-full text-sm"
                    />
                    <p className="mt-1.5 text-3xs text-textMuted">{t('modal:importUrlHint')}</p>
                  </div>
                )}
                {type === 'file' && (
                  <div>
                    {/* ui-standard-ignore: 整块可拖可点的文件选择区（虚线框 + 图标 + 说明），不是按钮形态 */}
                    <button
                      type="button"
                      onClick={() => fileRef.current?.click()}
                      className="flex w-full flex-col items-center gap-2 rounded-card border border-dashed border-border px-4 py-8 text-textMuted transition-colors hover:border-primary-400/60 hover:text-textSecondary"
                    >
                      <FileText size={22} />
                      <span className="text-xs">{fileName || t('modal:importFilePick')}</span>
                      <span className="text-3xs">{t('modal:importFileFormats')}</span>
                    </button>
                    <input
                      ref={fileRef}
                      type="file"
                      accept=".json,.txt,.md,application/json,text/plain,text/markdown"
                      className="hidden"
                      onChange={(e) => pickFile(e.target.files?.[0] || null)}
                    />
                  </div>
                )}
                {type === 'text' && (
                  <textarea
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    rows={10}
                    placeholder={t('modal:importTextPlaceholder')}
                    className="field min-h-[220px] w-full resize-y text-sm leading-relaxed"
                  />
                )}
              </div>

              {error && (
                <p className="mt-3 flex items-start gap-1.5 text-xs text-rose-400">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {error}
                </p>
              )}

              <div className="mt-4 flex items-center gap-2">
                <Button size="sm" onClick={resolve} disabled={!canResolve || busy}>
                  {busy ? t('modal:importResolving') : t('modal:importResolve')}
                </Button>
                <span className="text-3xs text-textMuted">{t('modal:importResolveHint')}</span>
              </div>
            </>
          )}

          {/* 解析后：预览 */}
          {resolved && (
            <div className="mt-4">
              <div className="rounded-card border border-border bg-surface p-4">
                <p className="text-sm font-medium text-textPrimary">
                  {t('modal:importPreviewTitle').replace('{platform}', resolved.origin || t('modal:importPlatformUnknown'))}
                </p>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-textSecondary">
                  <span>{t('modal:importPreviewMessages').replace('{n}', String(resolved.stats.messages))}</span>
                  <span>{t('modal:importPreviewTurns').replace('{u}', String(resolved.stats.user_messages)).replace('{a}', String(resolved.stats.assistant_messages))}</span>
                  {resolved.stats.attachments > 0 && (
                    <span className={resolved.stats.missing_attachments ? 'text-accent-400' : ''}>
                      {t('modal:importPreviewAttachments').replace('{n}', String(resolved.stats.attachments))}
                      {resolved.stats.missing_attachments > 0 &&
                        ' ' + t('modal:importPreviewMissing').replace('{n}', String(resolved.stats.missing_attachments))}
                    </span>
                  )}
                  {resolved.stats.first_at && (
                    <span>{t('modal:importPreviewRange')
                      .replace('{from}', resolved.stats.first_at.slice(0, 10))
                      .replace('{to}', (resolved.stats.last_at || resolved.stats.first_at).slice(0, 10))}</span>
                  )}
                </div>
                {resolved.warnings.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {resolved.warnings.map((w, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-3xs text-accent-400">
                        <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {w}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* ── 智能整理 ──
                  把这段对话提炼成「建这个人设要用的材料」：提示词（它是谁、怎么说话、
                  有哪些重大的事）、记忆、知识库。判据与创建助手同源，见后端 organize.py。 */}
              <div className="mt-3 rounded-card border border-border bg-surface p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-textPrimary">{t('modal:organizeTitle')}</p>
                    <p className="mt-0.5 text-3xs text-textMuted">{t('modal:organizeHint')}</p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    loading={organizing}
                    icon={<Sparkles size={13} />}
                    onClick={runOrganize}
                  >
                    {organizing ? t('modal:organizing') : t('modal:organize')}
                  </Button>
                </div>

                {organizeError && (
                  <p className="mt-2 flex items-start gap-1.5 text-3xs text-rose-400">
                    <AlertTriangle size={11} className="mt-0.5 shrink-0" /> {organizeError}
                  </p>
                )}

                {organized && (
                  <div className="mt-3 space-y-3">
                    <div>
                      <p className="text-3xs font-medium text-textSecondary">{t('modal:organizePromptLabel')}</p>
                      <textarea
                        value={organized.prompt}
                        onChange={(e) => setOrganized({ ...organized, prompt: e.target.value })}
                        rows={9}
                        className="field mt-1 min-h-[180px] w-full resize-y text-xs leading-relaxed"
                      />
                      <p className="mt-1 text-3xs text-textMuted">{t('modal:organizePromptHint')}</p>
                    </div>

                    {organized.memories.length > 0 && (
                      <div>
                        <p className="text-3xs font-medium text-textSecondary">
                          {t('modal:organizeMemoriesLabel').replace('{n}', String(organized.memories.length))}
                        </p>
                        <div className="mt-1 space-y-1">
                          {organized.memories.map((m, i) => (
                            <label key={i} className="flex items-start gap-2 text-3xs text-textSecondary">
                              <input
                                type="checkbox"
                                checked={!!pickMemories[i]}
                                onChange={(e) => setPickMemories({ ...pickMemories, [i]: e.target.checked })}
                                className="mt-0.5 rounded"
                              />
                              <span>{m.content}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    )}

                    {organized.knowledge.length > 0 && (
                      <div>
                        <p className="text-3xs font-medium text-textSecondary">
                          {t('modal:organizeKnowledgeLabel').replace('{n}', String(organized.knowledge.length))}
                        </p>
                        <div className="mt-1 space-y-1">
                          {organized.knowledge.map((d, i) => (
                            <label key={i} className="flex items-start gap-2 text-3xs text-textSecondary">
                              <input
                                type="checkbox"
                                checked={!!pickKnowledge[i]}
                                onChange={(e) => setPickKnowledge({ ...pickKnowledge, [i]: e.target.checked })}
                                className="mt-0.5 rounded"
                              />
                              <span>
                                <span className="text-textPrimary">{d.title}</span>
                                {d.summary && <span className="text-textMuted"> · {d.summary}</span>}
                              </span>
                            </label>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* 初步摘要：AI 一开口就知道「我们之前在别处聊过」。
                  将来真压缩时这条会被新摘要顶掉（账本的解锁点），所以只是一份起始说明。 */}
              <div className="mt-3">
                <label className="block text-xs font-medium text-textSecondary" htmlFor="chat-import-summary">
                  {t('modal:importSummaryLabel')}
                </label>
                <textarea
                  id="chat-import-summary"
                  value={summary}
                  onChange={(e) => setSummary(e.target.value)}
                  rows={3}
                  className="field mt-1 min-h-[76px] w-full resize-y text-xs leading-relaxed"
                />
                <p className="mt-1 text-3xs text-textMuted">{t('modal:importSummaryHint')}</p>
              </div>

              <p className="mt-3 text-3xs text-textMuted">{t('modal:importApplyHint')}</p>
              <div className="mt-3 flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => onReady({
                    ...resolved,
                    summary: summary.trim() || null,
                    transcript: { ...resolved.transcript, summary: summary.trim() || null },
                    // 只带用户勾掉之后剩下的那些：勾选是这次导入的最终取舍
                    organized: organized ? {
                      prompt: organized.prompt.trim(),
                      memories: organized.memories.filter((_, i) => pickMemories[i]),
                      knowledge: organized.knowledge.filter((_, i) => pickKnowledge[i]),
                    } : null,
                  })}
                >
                  {t('modal:importContinue')}
                </Button>
                <Button size="sm" variant="outline" onClick={() => { setResolved(null); setError('') }}>
                  {t('modal:importReselect')}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      <ConfigFallbackDialog
        needs={needsConfig}
        what={t('modal:organizeTitle')}
        onClose={() => setNeedsConfig(null)}
      />
    </div>
  )
}
