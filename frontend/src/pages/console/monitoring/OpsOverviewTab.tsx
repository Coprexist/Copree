// 运维总览 —— 一屏给不读日志的人看的数字。
//
// 版面与口径按外部实践收口（大厂面板 + Grafana dashboard best practices + NN/g 的仪表盘原则）：
//   一屏 6 张卡（每张都能回答"现在要不要处理"）、默认近 24 小时、每张卡悬停给口径与来源表、
//   能下钻的卡片直接点进已有审计/对话日志（带好筛选条件），没数据的格子显 "—" 而不是 0。
// 数字的口径全在后端 /admin/ops/overview（那里逐条写了定义），这里不另算一份。

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'

const RANGES = [1, 7, 30]

export default function OpsOverviewTab() {
  const t = useT()
  const navigate = useNavigate()
  const [days, setDays] = useState(1)
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    setData(null)
    api.get(`/admin/ops/overview?days=${days}`).then(setData).catch(console.error)
  }, [days])

  if (!data) return <p className="text-textMuted">{t('common.loading')}</p>

  const login = data.login || {}
  const attack = data.attack || {}
  const blocked = data.blocked || {}
  const conv = data.conversation || {}
  const people = data.people || {}
  const window = t(`admin.opsRange${days}`)
  // 限流那半是进程内计数：窗口跨过重启时只覆盖一部分，用 "—" 明说不知道，而不是报一个偏小的 0
  const throttleUnknown = blocked.throttle_partial && !blocked.throttle
  const repeatShare = attack.total_failures
    ? Math.round((attack.repeat_failures || 0) * 100 / attack.total_failures) : 0

  // 能下钻的卡片：跳到已有页签并把筛选带上（URL 是同一份真相，可收藏、可前进后退）
  type Card = {
    key: string; label: string; value: string; sub: string
    sub2?: string; tip: string; to?: string; warn?: boolean
  }
  const cards: Card[] = [
    {
      key: 'loginOk', label: t('admin.opsLoginOk'),
      value: t('admin.opsLoginOkValue', { n: login.success }),
      sub: t('admin.opsSubAccounts', { n: login.success_accounts }),
      tip: t('admin.opsTipLoginOk'), to: 'tab=logs&log_type=login',
    },
    {
      key: 'loginFail', label: t('admin.opsLoginFail'),
      value: t('admin.opsLoginFailValue', { n: login.failed }),
      sub: t('admin.opsSubAccounts', { n: login.failed_accounts }),
      sub2: t('admin.opsSubFailLocked', { n: data.lockouts }),
      tip: t('admin.opsTipLoginFail'), to: 'tab=logs&log_type=login_failed',
    },
    {
      key: 'suspects', label: t('admin.opsSuspects'),
      value: t('admin.opsSuspectsValue', { n: attack.suspects }),
      sub: attack.top ? t('admin.opsSubTop', { n: attack.top.failures }) : t('admin.opsNoSuspects'),
      sub2: attack.total_failures ? t('admin.opsSubRepeat', { p: repeatShare }) : '',
      tip: t('admin.opsTipSuspects', { min: attack.min_failures }),
      warn: (attack.suspects || 0) > 0,
    },
    {
      key: 'blocked', label: t('admin.opsBlocked'),
      value: t('admin.opsBlockedValue', { n: blocked.total }),
      sub: throttleUnknown
        ? t('admin.opsSubBlockedPartial', { a: blocked.lockout, b: blocked.throttle })
        : t('admin.opsSubBlocked', { a: blocked.lockout, b: blocked.throttle }),
      tip: t('admin.opsTipBlocked'), to: 'tab=logs&log_type=login_failed',
    },
    {
      key: 'turns', label: t('admin.opsTurns'),
      value: t('admin.opsTurnsValue', { n: conv.turns }),
      sub: t('admin.opsSubMessages', { n: (conv.group_messages || 0) + (conv.dm_messages || 0) }),
      tip: t('admin.opsTipTurns'), to: 'tab=convlog',
    },
    {
      key: 'newUsers', label: t('admin.opsNewUsers'),
      value: t('admin.opsNewUsersValue', { n: people.new_local }),
      sub: t('admin.opsSubTotalUsers', { n: people.total_local }),
      tip: t('admin.opsTipNewUsers'), to: 'tab=logs&log_type=register',
    },
  ]

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <label className="text-xs text-textSecondary">{t('admin.opsRangeLabel')}:</label>
        <select
          value={days}
          onChange={e => setDays(Number(e.target.value))}
          className="text-sm border border-border rounded-control px-2.5 py-1.5 bg-surface text-textPrimary"
        >
          {RANGES.map(d => <option key={d} value={d}>{t(`admin.opsRange${d}`)}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {cards.map(c => (
          <div
            key={c.key}
            title={c.tip}
            onClick={c.to ? () => navigate({ search: c.to as string }) : undefined}
            className={`rounded-card border border-border bg-surface p-4 ${c.to ? 'cursor-pointer hover:border-primary-400' : ''}`}
          >
            <div className="text-xs text-textSecondary">{c.label}</div>
            <div className={`mt-1 text-2xl font-semibold ${c.warn ? 'text-amber-400' : 'text-textPrimary'}`}>
              {c.value}
            </div>
            <div className="mt-1 text-xs text-textMuted">{c.sub}</div>
            {c.sub2 ? <div className="mt-0.5 text-xs text-textMuted">{c.sub2}</div> : null}
          </div>
        ))}
      </div>
      <p className="mt-4 text-xs text-textMuted leading-relaxed">
        {t('admin.opsFootnote', { window })}
      </p>
    </div>
  )
}
