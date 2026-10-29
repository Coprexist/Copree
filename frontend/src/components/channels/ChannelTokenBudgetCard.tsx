import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Coins, Loader2, RotateCcw } from 'lucide-react'

import { api } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import { Button, Card, Input } from '../ui'

/**
 * 通道对话额度 —— 每个 QQ 群 / 每个私聊的用量与剩余。
 *
 * 分工：
 *   策略（按什么算、默认多少、超限怎么回）属于通道插件，在插件的 config_schema 里声明，
 *   由 ChannelCard 上方的表单编辑；这里只读它推过来的默认值并展示。
 *   这份账（每个对话用了多少、还能用多少、单个对话要不要单独调）属于平台：
 *   只有平台看得见模型返回的 token。
 *
 * 为什么单独成组件：卡片本身已经很长，额度这块要自己取数、自己刷新，
 * 塞进主卡片会让主卡片多背一份状态。
 */
interface BudgetPolicy {
  meter_mode: string
  limit_tokens: number
  blocked_reply_enabled: boolean
  blocked_reply_text: string
}

interface BudgetConversation {
  conversation: string
  label: string
  meter_mode: string
  meter_mode_override: string
  limit_tokens: number
  limit_override: number | null
  used_tokens: number
  remaining_tokens: number
  blocked: boolean
}

interface Budget {
  policy: BudgetPolicy
  conversations: BudgetConversation[]
  default_blocked_reply: string
}

const num = (value: number) => Math.max(0, Math.round(value)).toLocaleString('zh-CN')

export default function ChannelTokenBudgetCard({ agentId, pluginId }: { agentId: number; pluginId: string }) {
  const t = useT()
  const [data, setData] = useState<Budget | null>(null)
  const [busy, setBusy] = useState('')
  const [editing, setEditing] = useState<{ conversation: string; value: string } | null>(null)
  const [msg, setMsg] = useState('')

  const base = `/me/agents/${agentId}/channels/${pluginId}/token-budget`

  const load = useCallback(async () => {
    try {
      setData(await api.get<Budget>(base))
    } catch {
      // 取不到就不显示这张卡：通道没配置好时它是空的，弹个错反而误导
      setData(null)
    }
  }, [base])

  useEffect(() => { void load() }, [load])

  if (!data) return null
  const { policy, conversations } = data
  const meterLabel = (mode: string) => t(mode === 'total' ? 'tool:channel.token.meter.total' : 'tool:channel.token.meter.billable')

  const saveLimit = async (conversation: string, value: string, clear: boolean) => {
    setBusy('save:' + conversation)
    setMsg('')
    try {
      await api.put(base + '/conversation', {
        conversation,
        ...(clear ? { clear: true } : { limit_tokens: Number(value.replace(/[^0-9]/g, '')) || 0 }),
      })
      setEditing(null)
      await load()
    } catch (e: any) {
      setMsg(e?.detail || e?.message || String(e))
    } finally {
      setBusy('')
    }
  }

  const reset = async (conversation?: string) => {
    setBusy('reset:' + (conversation || 'all'))
    setMsg('')
    try {
      await api.post(base + '/reset', { conversation: conversation || null })
      await load()
    } catch (e: any) {
      setMsg(e?.detail || e?.message || String(e))
    } finally {
      setBusy('')
    }
  }

  return (
    <Card title={t('tool:channel.token.title')} icon={<Coins size={14} className="text-primary-400" />}>
      {/* 默认值只读：它来自插件的配置项（config_schema），在卡片上方的表单里改 */}
      <div className="rounded-card border border-border bg-canvas/50 px-3 py-2 text-3xs text-textSecondary space-y-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>{t('tool:channel.token.perConversation')} <span className="text-textPrimary">{num(policy.limit_tokens)}</span></span>
          <span className="text-textMuted">·</span>
          <span>{meterLabel(policy.meter_mode)}</span>
          <span className="text-textMuted">·</span>
          <span className={policy.blocked_reply_enabled ? 'text-mint-400' : 'text-textMuted'}>
            {t(policy.blocked_reply_enabled ? 'tool:channel.token.autoReplyOn' : 'tool:channel.token.autoReplyOff')}
          </span>
        </div>
        {policy.blocked_reply_enabled && (
          <div className="text-textMuted line-clamp-2">
            {policy.blocked_reply_text || data.default_blocked_reply}
          </div>
        )}
        <div className="text-textMuted">{t('tool:channel.token.hintFromPlugin')}</div>
      </div>

      {conversations.length === 0 ? (
        <p className="text-3xs text-textMuted">{t('tool:channel.token.empty')}</p>
      ) : (
        <div className="space-y-1.5">
          {conversations.map((row) => {
            const ratio = row.limit_tokens > 0 ? Math.min(100, Math.round(row.used_tokens / row.limit_tokens * 100)) : 100
            const tone = row.blocked ? 'bg-rose-500' : ratio >= 80 ? 'bg-amber-500' : 'bg-mint-400'
            return (
              <div key={row.conversation} className="rounded-card border border-border px-3 py-2 space-y-1.5">
                <div className="flex items-center gap-2 flex-wrap text-xs">
                  <span className="text-textPrimary truncate max-w-[14rem]" title={row.label || row.conversation}>
                    {row.label || row.conversation}
                  </span>
                  {row.blocked && (
                    <span className="text-3xs px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20">
                      {t('tool:channel.token.blocked')}
                    </span>
                  )}
                  {row.limit_override !== null && (
                    <span className="text-3xs px-1.5 py-0.5 rounded bg-elevated text-textSecondary">
                      {t('tool:channel.token.overridden')}
                    </span>
                  )}
                  <span className="flex-1" />
                  <span className="text-3xs text-textMuted">
                    {t('tool:channel.token.usedOf', { used: num(row.used_tokens), limit: num(row.limit_tokens) })}
                  </span>
                  <span className={'text-3xs ' + (row.blocked ? 'text-rose-400' : 'text-mint-400')}>
                    {t('tool:channel.token.remaining', { n: num(row.remaining_tokens) })}
                  </span>
                </div>
                <div className="h-1 rounded bg-elevated overflow-hidden">
                  <div className={'h-full transition-all ' + tone} style={{ width: ratio + '%' }} />
                </div>
                {editing?.conversation === row.conversation ? (
                  <div className="flex items-center gap-2 flex-wrap">
                    <Input
                      value={editing.value}
                      onChange={e => setEditing({ conversation: row.conversation, value: e.target.value })}
                      name={'channel-token-limit-' + row.conversation}
                      fieldSize="sm"
                      className="w-40"
                      placeholder={t('tool:channel.token.limitPlaceholder')}
                    />
                    <Button size="xs" variant="primary" loading={busy === 'save:' + row.conversation}
                      onClick={() => saveLimit(row.conversation, editing.value, false)}>{t('tool:channel.token.save')}</Button>
                    <Button size="xs" variant="secondary" loading={busy === 'save:' + row.conversation}
                      onClick={() => saveLimit(row.conversation, '', true)}>{t('tool:channel.token.followDefault')}</Button>
                    <Button size="xs" variant="ghost" onClick={() => setEditing(null)}>{t('tool:channel.token.cancel')}</Button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <Button size="xs" variant="secondary"
                      onClick={() => setEditing({ conversation: row.conversation, value: String(row.limit_tokens) })}>
                      {t('tool:channel.token.adjust')}
                    </Button>
                    <Button size="xs" variant="ghost" loading={busy === 'reset:' + row.conversation}
                      onClick={() => reset(row.conversation)}>
                      <RotateCcw size={11} /> {t('tool:channel.token.reset')}
                    </Button>
                    <span className="text-3xs text-textMuted">{meterLabel(row.meter_mode)}</span>
                  </div>
                )}
              </div>
            )
          })}
          <div className="flex items-center gap-2">
            <Button size="xs" variant="ghost" loading={busy === 'reset:all'} onClick={() => reset()}>
              {t('tool:channel.token.resetAll')}
            </Button>
          </div>
        </div>
      )}

      {!!msg && (
        <div className="flex items-center gap-2 text-3xs rounded-card border px-3 py-2 border-rose-500/30 bg-rose-500/10 text-rose-400">
          <AlertTriangle size={12} className="shrink-0" />
          <span className="min-w-0 truncate">{msg}</span>
        </div>
      )}
      {busy.startsWith('reset:') && !msg && <Loader2 size={11} className="animate-spin text-textMuted" />}
    </Card>
  )
}
