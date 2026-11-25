/**
 * 「实际使用」stepper —— 这个 AI 现在真正打哪条配置，以及前面几档为什么跳过。
 *
 * 数据只有一处来源：GET /agents/{id}/api-chain 返回的 Resolution.chain，与真实请求共用
 * 同一个 resolve_llm —— 显示即实跑，不在这里另写一套判断。
 *
 * 四节点常驻、等宽，不因命中项重排：命中的高亮，其余写**具体原因**（没绑配置 / 没有可用
 * 额度 / Key 不可用），而不是笼统的「跳过」；④ 平时灰暗，三档都没命中时变红并给「去绑定」。
 * 这是结论，不是监控面板：不呼吸、不闪烁、不轮询。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { useAuth } from '../../context/AuthContext'
import { useT } from '../../i18n/I18nContext'
import { consoleTab } from '../../utils/routes'
import { Button } from '../ui'

interface ChainStep {
  level: string
  status: string
  reason: string
  label: string
  base_url: string
  model: string
}

export interface ApiChain {
  source: string
  usable: boolean
  chat_model: string | null
  missing_reason: string
  chain: ChainStep[]
  profile_id: number | null
}

const LEVELS = ['bound', 'pool', 'default', 'fail'] as const

/** 判定结论里字段以外的展示口径都在这里：界面文案按 reason 词表查，不另造一套 */
function useReasonText() {
  const t = useT()
  return (step: ChainStep | undefined): string => {
    if (!step) return '—'
    if (step.status === 'hit') return '● ' + t('apiConfig:runtimeHit')
    switch (step.reason) {
      case 'no_bind': return t('apiConfig:runtimeSkipNoBind')
      case 'no_key':
      case 'bad_key': return t('apiConfig:runtimeSkipBadKey')
      case 'no_quota':
      case 'no_member': return t('apiConfig:runtimeSkipNoQuota')
      case 'not_configured': return t('apiConfig:runtimeSkipNoBind')
      default: return t('apiConfig:runtimeSkipOther')
    }
  }
}

export default function RuntimeChain({
  agentId,
  onData,
  onGoBind,
}: {
  agentId: number
  /** 父级拿同一份结论（配置列表的「生效中」徽标要用它，免得再请求一次） */
  onData?: (chain: ApiChain | null) => void
  /** ① / ④ 的目标：滚到下面的配置列表 */
  onGoBind?: () => void
}) {
  const t = useT()
  const navigate = useNavigate()
  const { user } = useAuth()
  const reasonText = useReasonText()
  const [data, setData] = useState<ApiChain | null>(null)

  useEffect(() => {
    let alive = true
    setData(null)
    api.get<ApiChain>('/agents/' + agentId + '/api-chain')
      .then((r) => { if (alive) { setData(r); onData?.(r) } })
      .catch(() => { if (alive) { setData(null); onData?.(null) } })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId])

  if (!data) return null

  const byLevel = new Map(data.chain.map((s) => [s.level, s]))
  const failed = !data.usable && data.source === 'none'
  const title = (level: string) => level === 'bound'
    ? t('apiConfig:runtimeLevelBind')
    : level === 'pool' ? t('apiConfig:runtimeLevelPool')
    : level === 'default' ? t('apiConfig:runtimeLevelDefault')
    : t('apiConfig:runtimeLevelFail')
  // 第二行：命中给「配置名」，跳过给原因；④ 只报结论
  const sub = (level: string, step: ChainStep | undefined) => {
    if (level === 'fail') {
      return failed ? t('apiConfig:runtimeFail') : t('apiConfig:runtimeSkipOther')
    }
    if (step?.status === 'hit') return step.label || t('apiConfig:runtimeHit')
    return reasonText(step)
  }

  return (
    <section className="space-y-1.5">
      <div className="flex items-center gap-2">
        <h4 className="text-xs font-semibold text-textPrimary">{t('apiConfig:runtimeChainTitle')}</h4>
        {user?.role === 'admin' && (
          <Button variant="ghost" size="sm" className="ml-auto"
            onClick={() => navigate(consoleTab('convlog', { agent: agentId }))}>
            {t('apiConfig:openRequestLog')} →
          </Button>
        )}
      </div>

      {/* 结论链：一行胶囊，命中项描边提亮、跳过项压暗；窄屏自然换行，不重排层级 */}
      <div className="flex flex-wrap items-center gap-1.5">
        {LEVELS.map((level, i) => {
          const step = byLevel.get(level)
          const hit = step?.status === 'hit'
          const isFail = level === 'fail' && failed
          const clickable = (level === 'bound' || level === 'fail') && !!onGoBind
          const tone = hit
            ? 'border-mint-500/40 bg-mint-500/10'
            : isFail
              ? 'border-rose-500/40 bg-rose-500/10'
              : 'border-border/70 opacity-55'
          const pill = 'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 ' + tone
          const body = (
            <>
              <span className="text-3xs text-textMuted">{title(level)}</span>
              <span className={'text-xs ' + (hit ? 'text-textPrimary' : 'text-textMuted')}>
                {sub(level, step)}
              </span>
              {hit && step?.model && (
                <span className="text-3xs text-textMuted">· {step.model}</span>
              )}
            </>
          )
          return (
            <div key={level} className="contents">
              {i > 0 && <span className="text-3xs text-textMuted">›</span>}
              {clickable ? (
                // ui-standard-ignore: 结论胶囊，整块可点，不是标准按钮
                <button type="button" onClick={onGoBind}
                  className={pill + ' card-interactive text-left'}>
                  {body}
                </button>
              ) : (
                <span className={pill}>{body}</span>
              )}
            </div>
          )
        })}
      </div>

      {failed && onGoBind && (
        <button type="button" onClick={onGoBind}
          className="text-3xs text-accent-400 hover:underline">
          {t('apiConfig:goBind')}
        </button>
      )}
    </section>
  )
}
