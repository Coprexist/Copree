/**
 * 对话日志浏览器 —— AI 详情页与管理台「对话日志」共用（单一实现，勿各自手写）。
 *
 * 两级：先按状态帧身份铺一屏大按钮（各状态的请求体前缀本就不同，先看有哪些状态），
 * 点进去看这段状态的最近一次完整请求体、与上一条的增量、以及这段状态的历史。
 * 「停在哪段状态、看的是哪一条」写进查询参数（state / log），刷新和后退都回到原地。
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { api } from '../../api/client'
import { saveElementAsHtml } from '../../utils/exportHtml'
import { useT } from '../../i18n/I18nContext'
import RequestBodyViewer from './RequestBodyViewer'
import { RunStatusChip } from './RunStatus'
import {
  StateChip, groupByState, logStateOf, stateKeyOf, stateFrameOfKey, type LogStateFrame,
} from './LogState'

interface LogSummary {
  id: number
  message_count: number
  status?: string
  state_frame?: LogStateFrame
  created_at: string | null
}

interface Props {
  agentId: number
  /** 两种角色看到的接口前缀不同（用户端 / 管理台），其余完全一致 */
  basePath?: string
  /** 有导出接口的角色才传（管理台没有） */
  exportLog?: (id: number, format: 'json' | 'md') => void
  limit?: number
}

const timeOf = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString('zh-CN') : ''

/** 改变量里的一截：多出来的 / 没了的，各自连着那几条消息 */
function Hunk({ label, removed, messages, names, renderAll }: {
  label: string; removed?: boolean; messages: any[]; names?: Record<string, string>; renderAll?: boolean
}) {
  return (
    <div className="space-y-1.5">
      <div className={`text-3xs ${removed ? 'text-rose-400' : 'text-mint-400'}`}>{label}</div>
      <RequestBodyViewer
        messages={messages} legend={false} mentionNames={names} renderAll={renderAll}
        tone={removed ? 'removed' : 'normal'}
      />
    </div>
  )
}

export default function LogBrowser({ agentId, basePath = '/conversation-log', exportLog, limit = 30 }: Props) {
  const t = useT()
  const [searchParams, setSearchParams] = useSearchParams()
  const [logs, setLogs] = useState<LogSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [detail, setDetail] = useState<any>(null)
  const [delta, setDelta] = useState<any>(null)
  const [bodyLoading, setBodyLoading] = useState(false)
  const [showDelta, setShowDelta] = useState(false)
  /**
   * 导出用的 DOM：连按钮那一行一起序列化（导出件里「原始 JSON」的开关在那里），
   * 站内专有的下载按钮标了 data-export-skip，导出时会被摘掉
   */
  const exportRef = useRef<HTMLDivElement>(null)
  const [rawBody, setRawBody] = useState(false)
  // 导出前先把分片渲染补完，否则克隆下来的 DOM 会缺掉还没补上的块
  const [exportAll, setExportAll] = useState(false)
  // 导出要把分片补完再落盘，这段时间按钮得看得出在干活
  const [exporting, setExporting] = useState(false)
  // 「原始 JSON」按钮在这一行、开关组在正文里，两边靠这个 id 对上（见 RequestBodyViewer）
  const rawSwitchId = useId()

  const stateKey = searchParams.get('state')
  const wantedLogId = Number(searchParams.get('log')) || null

  useEffect(() => {
    let alive = true
    setLoading(true)
    setFailed(false)
    api.get(`${basePath}/agents/${agentId}/logs?limit=${limit}`)
      .then(data => { if (alive) setLogs(data || []) })
      .catch(() => { if (alive) { setLogs([]); setFailed(true) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [agentId, basePath, limit])

  const groups = useMemo(() => groupByState(logs, logStateOf), [logs])
  const active = groups.find(group => stateKeyOf(group.frame) === (stateKey ?? '')) ?? null
  const items = active?.items ?? []
  // 选中项失效（换了个 AI、日志被裁掉）就退回这段状态里最新的一条
  const currentId = wantedLogId && items.some(log => log.id === wantedLogId) ? wantedLogId : (items[0]?.id ?? null)
  // 列表是新到旧，同一段状态里的"上一条"就是它的下一条
  const prevId = currentId ? (items[items.findIndex(log => log.id === currentId) + 1]?.id ?? null) : null

  useEffect(() => {
    if (!currentId) {
      setDetail(null); setDelta(null)
      return
    }
    let alive = true
    setBodyLoading(true)
    setShowDelta(false)
    setRawBody(false)
    const path = `${basePath}/agents/${agentId}/logs/${currentId}`
    Promise.all([
      api.get(path),
      prevId ? api.get(`${path}/delta?prev_id=${prevId}`) : Promise.resolve(null),
    ])
      .then(([body, diff]) => { if (alive) { setDetail(body); setDelta(diff) } })
      .catch(() => { if (alive) { setDetail(null); setDelta(null) } })
      .finally(() => { if (alive) setBodyLoading(false) })
    return () => { alive = false }
  }, [agentId, basePath, currentId, prevId])

  /** 改查询参数（合并而不是覆盖：同一个 URL 上还挂着页面自己的 tab 等参数） */
  const go = (patch: { state?: string | null; log?: string | null }) => {
    const next = new URLSearchParams(searchParams)
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) next.delete(key)
      else next.set(key, value)
    }
    setSearchParams(next)
  }

  /**
   * 下载 HTML：把当前渲染结果原样落盘。
   * 这条不走后端导出接口——后端只给纯文本，markdown/公式/代码高亮是前端渲染的；
   * 也正因为纯前端，管理台的日志浏览器同样能用。
   */
  const exportHtml = () => {
    const el = exportRef.current
    if (!el || !currentId || exporting) return
    setExporting(true)
    setExportAll(true)
    // 两帧之后再克隆：第一帧 React 提交全部块，第二帧布局稳定
    requestAnimationFrame(() => requestAnimationFrame(() => {
      saveElementAsHtml(el, 'log-' + currentId + '.html', '#' + currentId)
      setExportAll(false)
      setExporting(false)
    }))
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-textMuted py-6 justify-center">
        <Loader2 size={14} className="animate-spin" /> {t('common.loading')}
      </div>
    )
  }
  if (failed) return <p className="text-sm text-textMuted py-6 text-center">{t('logs:loadFailed')}</p>
  if (groups.length === 0) return <p className="text-sm text-textMuted py-6 text-center">{t('logs:noLogs')}</p>

  // ── 第一级：状态帧一览（大按钮）──
  if (!active) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {groups.map(group => {
          const key = stateKeyOf(group.frame)
          const newest = group.items[0]
          return (
            <button
              key={key || 'none'}
              type="button"
              onClick={() => go({ state: key, log: null })}
              className="text-left p-4 rounded-card border border-border bg-canvas hover:border-primary-500/50 hover:bg-elevated transition-colors"
            >
              <StateChip frame={group.frame} />
              <div className="mt-2 flex items-baseline gap-1.5">
                <span className="text-xl font-semibold text-textPrimary">{group.items.length}</span>
                <span className="text-3xs text-textMuted">{t('logs:stateRequests')}</span>
              </div>
              <div className="text-3xs text-textMuted mt-1">{timeOf(newest.created_at)}</div>
            </button>
          )
        })}
      </div>
    )
  }

  // ── 第二级：某段状态的请求体与历史 ──
  const comparable = Boolean(delta?.prev_log_id)
  const changeLine = comparable
    ? t('logs:deltaSummary')
        .replace('{prev}', String(delta.prev_log_id))
        .replace('{added}', String(delta.added_count))
        .replace('{removed}', String(delta.removed_count))
        .replace('{shared}', String(delta.shared_count))
    : ''

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => go({ state: null, log: null })}
          className="flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 transition-colors"
        >
          <ArrowLeft size={13} /> {t('logs:stateBack')}
        </button>
        <StateChip frame={active.frame} />
        <span className="text-3xs text-textMuted">{items.length} {t('logs:stateRequests')}</span>
        <div className="ml-auto flex items-center rounded-control border border-border overflow-hidden">
          {([false, true] as const).map(deltaMode => (
            <button
              key={String(deltaMode)}
              type="button"
              disabled={deltaMode && !comparable}
              onClick={() => setShowDelta(deltaMode)}
              className={`text-3xs px-2 py-1 transition-colors disabled:opacity-40 ${
                showDelta === deltaMode ? 'bg-primary-500/10 text-primary-400' : 'text-textMuted hover:text-textSecondary'
              }`}
            >
              {deltaMode ? t('logs:bodyDelta') : t('logs:bodyFull')}
            </button>
          ))}
        </div>
      </div>
      <p className="text-3xs text-textMuted">{showDelta ? (comparable ? changeLine : t('logs:deltaFirst')) : ''}</p>

      {/* 两栏共用一个高度上限：各自的滚动区等高，左右才对齐（限高原先各挂各的，图例把右侧顶下去） */}
      <div className="flex flex-col lg:flex-row gap-3 max-h-[70vh] min-h-0">
        <aside className="lg:w-60 shrink-0 min-h-0 overflow-y-auto space-y-0.5">
          <div className="text-3xs text-textMuted px-2 pb-1">{t('logs:stateHistory')}</div>
          {items.map(log => (
            <button
              key={log.id}
              type="button"
              onClick={() => go({ state: stateKey, log: String(log.id) })}
              className={`w-full text-left px-2 py-1.5 rounded-control border transition-colors ${
                log.id === currentId ? 'border-primary-500/60 bg-primary-500/5' : 'border-transparent hover:bg-canvas'
              }`}
            >
              <div className="flex items-center gap-1.5">
                <span className="text-3xs font-mono text-textMuted">#{log.id}</span>
                <RunStatusChip status={log.status} />
              </div>
              <div className="text-3xs text-textMuted">{log.message_count} · {timeOf(log.created_at)}</div>
            </button>
          ))}
        </aside>

        <div ref={exportRef} className="flex-1 min-w-0 flex flex-col min-h-0">
          {currentId && (
            <div data-export-stick-head className="flex items-center gap-2 py-1 sticky top-0 z-20 bg-surface">
              <span className="text-3xs font-mono text-textMuted">#{currentId}</span>
              {exportLog && (
                <>
                  <button
                    type="button"
                    data-export-skip
                    onClick={() => exportLog(currentId, 'json')}
                    className="text-3xs text-textMuted hover:text-textSecondary transition-colors"
                  >
                    {t('logs:downloadJson')}
                  </button>
                  <button
                    type="button"
                    data-export-skip
                    onClick={() => exportLog(currentId, 'md')}
                    className="text-3xs text-textMuted hover:text-textSecondary transition-colors"
                  >
                    {t('logs:downloadMd')}
                  </button>
                </>
              )}
              <button
                type="button"
                data-export-skip
                onClick={exportHtml}
                disabled={detail === null || exporting}
                title={t('logs:downloadHtmlHint')}
                className="text-3xs text-textMuted hover:text-textSecondary transition-colors disabled:opacity-40"
              >
                {exporting ? t('logs:downloadHtmlPreparing') : t('logs:downloadHtml')}
              </button>
              {/* 原始 JSON 的开关跟下载按钮并排；改变量视图里没有整段 JSON，那条路上不摆 */}
              {!showDelta && (
                <button
                  type="button"
                  onClick={() => setRawBody(v => !v)}
                  data-switch={rawSwitchId}
                  data-value={rawBody ? 'raw' : 'segments'}
                  className="ml-auto text-3xs px-1.5 py-0.5 rounded border border-border text-textMuted hover:text-textSecondary transition-colors"
                >
                  <span data-when="segments">{t('logs:viewRaw')}</span>
                  <span data-when="raw">{t('logs:viewSegments')}</span>
                </button>
              )}
            </div>
          )}
          {/* 限高与滚动挪进 RequestBodyViewer：滚动条要落在图例下面，不能从图例右侧穿上去；
              这里只负责占满剩余高度 */}
          <div className="min-h-0 flex flex-col">
          {bodyLoading ? (
            <div className="flex items-center gap-2 text-xs text-textMuted py-6 justify-center">
              <Loader2 size={13} className="animate-spin" /> {t('common.loading')}
            </div>
          ) : showDelta && comparable ? (
            // 改变量：按原顺序摆——相同的那几段折叠成一行，多出来/没了的各自成截
            <div className="space-y-2">
              {delta.ops.map((op: any, index: number) => op.tag === 'equal' ? (
                <div key={index} className="text-3xs text-textMuted text-center py-0.5">
                  {t('logs:hunkSame').replace('{n}', String(op.count))}
                </div>
              ) : (
                <div key={index} className="space-y-2">
                  {op.tag !== 'insert' && (
                    <Hunk
                      removed
                      label={t('logs:hunkRemoved').replace('{n}', String((op.removed || op.messages || []).length))}
                      messages={op.tag === 'replace' ? op.removed : op.messages}
                      names={detail?.mention_names}
                    />
                  )}
                  {op.tag !== 'delete' && (
                    <Hunk
                      label={t('logs:hunkAdded').replace('{n}', String((op.added || op.messages || []).length))}
                      messages={op.tag === 'replace' ? op.added : op.messages}
                      names={detail?.mention_names}
                    />
                  )}
                </div>
              ))}
            </div>
          ) : (
            <RequestBodyViewer
              renderAll={exportAll}
              raw={rawBody}
              onToggleRaw={() => setRawBody(v => !v)}
              rawSwitchId={rawSwitchId}
              messages={detail?.messages || []}
              mentionNames={detail?.mention_names}
            />
          )}
          </div>
        </div>
      </div>
    </div>
  )
}
