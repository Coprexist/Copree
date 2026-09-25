/**
 * DSH 桥接 —— 管理 → 插件 里的状态卡。
 *
 * 闸门在 DSH 侧：人要在 DSH 的「设置 → Copree」里点同意接入，插件才开始发心跳。
 * 没同意之前这里只会显示「未检测到」——所以这张卡只做两件事：如实显示状态、允许断开。
 *
 * 为什么不在 Copree 里再点一次同意：那道闸门保护的是 Copree，而 Copree 侧本来就只有管理员
 * 能发指令；同一个管理员点两次不是两道锁，只是假安全感。Coproee 能做的只有「更窄」——
 * 断开就是不再往 DSH 转发，方向单调，不会放宽 DSH 的决定。
 *
 * 对话本身不在这里挤着（DSH 的会话有上下文、工具与长输出），点「打开对话」进 /dsh 独立页。
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCircle2, Circle, ExternalLink, Plug, RefreshCw, Unplug, XCircle } from 'lucide-react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'

interface Status {
  detected: boolean
  state: 'online' | 'unreachable' | 'offline'
  version?: string
  last_seen_seconds?: number | null
  secret_configured?: boolean
  detail?: string
}

export default function DshBridgeTab() {
  const t = useT()
  const navigate = useNavigate()
  const [status, setStatus] = useState<Status | null>(null)

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api.get<Status>('/admin/dsh/status'))
    } catch (e: any) {
      setStatus({ detected: false, state: 'offline', detail: e && e.message ? e.message : String(e) })
    }
  }, [])

  useEffect(() => { loadStatus() }, [loadStatus])
  useEffect(() => {
    const timer = setInterval(loadStatus, 5000)
    return () => clearInterval(timer)
  }, [loadStatus])

  /** 断开：清掉本端注册（DSH 侧不受影响，下一次心跳就会重新登记） */
  const disconnect = useCallback(async () => {
    try {
      await api.post('/dsh-bridge/forget', {})
    } finally {
      void loadStatus()
    }
  }, [loadStatus])

  const icon = status && status.state === 'online'
    ? <CheckCircle2 size={14} className="text-mint-400" />
    : status && status.state === 'unreachable'
      ? <XCircle size={14} className="text-rose-400" />
      : <Circle size={14} className="text-textMuted" />

  const label = status && status.state === 'online'
    ? t('tool:dsh.state.online')
    : status && status.state === 'unreachable'
      ? t('tool:dsh.state.unreachable')
      : t('tool:dsh.state.offline')

  return (
    <div className="rounded-card border border-border bg-elevated/30 p-4 mb-4">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-medium text-textPrimary flex items-center gap-1.5">
          <Plug size={15} className="text-primary-400" /> {t('tool:dsh.title')}
        </h3>
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1 text-xs text-textSecondary">
            {icon}{label}{status && status.version ? ' · v' + status.version : ''}
          </span>
          <button onClick={loadStatus} className="icon-btn-sm" aria-label={t('tool:dsh.refresh')} title={t('tool:dsh.refresh')}>
            <RefreshCw size={13} />
          </button>
          {status && status.detected && (
            <button
              onClick={() => { void disconnect() }}
              className="icon-btn-sm"
              aria-label={t('tool:dsh.disconnect')}
              title={t('tool:dsh.disconnect')}
            >
              <Unplug size={13} />
            </button>
          )}
          <button
            onClick={() => navigate('/dsh')}
            className="flex items-center gap-1 px-2.5 py-1 rounded-control text-xs font-medium bg-primary-500/10 text-primary-400 hover:bg-primary-500/20 border border-primary-400/20 transition-colors"
          >
            <ExternalLink size={12} /> {t('tool:dsh.open')}
          </button>
        </div>
      </div>
      <p className="text-xs text-textMuted">{t('tool:dsh.desc')}</p>
      <p className="mt-0.5 text-3xs text-textMuted">{t('tool:dsh.limited')}</p>
      {status && status.state === 'offline' && (
        <p className="mt-1 text-3xs text-textMuted">{t('tool:dsh.consentHint')}</p>
      )}
    </div>
  )
}
