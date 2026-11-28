/**
 * 一个场景的卡片：总开关 + 有序配置序列 + 每格子开关 + 失败红点
 *
 * 骨架与 ProviderCards 完全一致（card → 头部一行 → AutoHeight 展开 → 内层行），
 * 「默认配置」页与「LLM 厂商 / 语音厂商」页因此是同一套卡片，而不是又一版长相。
 * 差异只在内容：这里每行是「场景序列里的一格」，多了顺序与子开关。
 *
 * 保存用「整体保存」：排序、子开关、增删在界面上是一次操作，拆成多个请求会出现
 * 「顺序存了一半」的中间态（PUT /user/api-config/scenarios/{key}/routes）。
 *
 * 默认收起：这一页要的是「五个场景各自现在用哪条」的全貌，细节点开再看。
 */
import { useState } from 'react'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import { api, errorText } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import { AutoHeight, Badge, Button, Dialog, IconButton } from '../ui'
import Toggle from '../Toggle'

export interface ScenarioRoute {
  profile_id: number
  profile_name: string
  provider_name: string
  position: number
  enabled: boolean
  status: { state: string; reason: string | null; unread: boolean } | null
}

export interface Scenario {
  key: string
  kind: 'llm' | 'voice'
  enabled: boolean
  routes: ScenarioRoute[]
  first_enabled_profile_id: number | null
}

export interface ProfileOption {
  id: number
  label: string
}

export default function ScenarioCard({
  scenario,
  name,
  desc,
  profiles,
  onChanged,
}: {
  scenario: Scenario
  name: string
  desc: string
  profiles: ProfileOption[]
  onChanged: () => Promise<void> | void
}) {
  const t = useT()
  const [picking, setPicking] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState('')

  // 防御：接口字段缺失时退化成空序列，不让整页白屏——失败应该局部化
  const routes = scenario.routes ?? []
  const enabledRoutes = routes.filter((r) => r.enabled)
  const current = routes.find((r) => r.profile_id === scenario.first_enabled_profile_id)
  // 未读红点总数：收起时也要看得见，否则失败藏在折叠里等于没有告警
  const unreadCount = routes.filter((r) => r.status?.unread).length

  const currentText = scenario.enabled
    ? (current
        ? t('apiConfig:scenarioCurrent', {
            name: current.provider_name + ' · ' + current.profile_name,
          })
        : t('apiConfig:scenarioNoRoute'))
    : t('apiConfig:scenarioOff')

  /** 整体保存：数组顺序即尝试顺序；enabled 是子开关 */
  const persist = async (next: ScenarioRoute[]) => {
    try {
      await api.put('/user/api-config/scenarios/' + scenario.key + '/routes', {
        routes: next.map((r) => ({ profile_id: r.profile_id, enabled: r.enabled })),
      })
      setError('')
      await onChanged()
    } catch (e) {
      setError(errorText(e, t('apiConfig:loadFailed')))
    }
  }

  const toggleTotal = async (enabled: boolean) => {
    try {
      await api.put('/user/api-config/scenarios/' + scenario.key, { enabled })
      setError('')
      await onChanged()
    } catch (e) {
      setError(errorText(e, t('apiConfig:loadFailed')))
    }
  }

  const move = (index: number, delta: number) => {
    const next = [...routes]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    void persist(next)
  }

  /** 红点：用户读过才清（后端只清 unread，历史 state 留着判断是否重复发消息） */
  const ack = async (profileId: number) => {
    try {
      await api.post(
        '/user/api-config/scenarios/' + scenario.key + '/routes/' + profileId + '/ack', {},
      )
      await onChanged()
    } catch (e) {
      setError(errorText(e, t('apiConfig:loadFailed')))
    }
  }

  const reasonText = (reason: string | null) => {
    if (reason === 'no_quota' || reason === 'no_member') return t('apiConfig:runtimeSkipNoQuota')
    if (reason === 'no_key' || reason === 'bad_key') return t('apiConfig:runtimeSkipBadKey')
    return t('apiConfig:runtimeSkipOther')
  }

  const addable = profiles.filter((p) => !routes.some((r) => r.profile_id === p.id))

  return (
    <div className="card">
      <div className="flex items-center gap-2 pr-3">
        {/* 卡片头整行可点；箭头在左：这一层是场景，右箭头留给里面的配置档行 */}
        <Button
          variant="ghost"
          size="sm"
          className="flex-1 min-w-0 !justify-start !gap-3 !h-auto !pl-3 !pr-0 !py-2.5"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
        >
          <ChevronDown
            size={14}
            className={'shrink-0 text-textMuted transition-transform ' + (expanded ? 'rotate-180' : '')}
          />
          <span className="text-sm font-medium text-primary-400 truncate">{name}</span>
          <span className="text-3xs text-textMuted truncate">{currentText}</span>
          {unreadCount > 0 && <Badge tone="rose">{t('apiConfig:scenarioFailedShort')}</Badge>}
        </Button>
        <Toggle
          size="sm"
          checked={scenario.enabled}
          onChange={(v) => void toggleTotal(v)}
          ariaLabel={name}
        />
      </div>

      {error && <p className="text-xs text-rose-400 px-3 pb-2">{error}</p>}

      <AutoHeight open={expanded} fadeKey={scenario.key}>
        <div className="border-t border-border/50 px-2 py-1.5 space-y-1">
          {desc && <p className="text-3xs text-textMuted px-1 pb-1">{desc}</p>}

          {routes.length === 0 ? (
            <p className="text-3xs text-textMuted px-1">{t('apiConfig:scenarioEmpty')}</p>
          ) : (
            routes.map((route, index) => (
              <div key={route.profile_id} className="flex items-center gap-2 rounded-control px-2 py-1.5">
                <span className="text-3xs text-textMuted w-4 shrink-0">{index + 1}</span>
                <span className={
                  'text-sm truncate ' + (route.enabled ? 'text-textPrimary' : 'text-textMuted line-through')
                }>
                  {route.provider_name} · {route.profile_name}
                </span>
                {route.status?.unread && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-auto px-1 text-rose-400"
                    onClick={() => void ack(route.profile_id)}
                    title={t('apiConfig:scenarioFailed', { reason: reasonText(route.status.reason) })}
                  >
                    {t('apiConfig:scenarioFailedShort')}
                  </Button>
                )}
                <div className="ml-auto flex items-center gap-1 shrink-0">
                  <IconButton
                    size="sm"
                    label={t('apiConfig:scenarioMoveUp')}
                    icon={<ChevronUp size={14} />}
                    disabled={index === 0}
                    onClick={() => move(index, -1)}
                  />
                  <IconButton
                    size="sm"
                    label={t('apiConfig:scenarioMoveDown')}
                    icon={<ChevronDown size={14} />}
                    disabled={index === routes.length - 1}
                    onClick={() => move(index, 1)}
                  />
                  {/* 用 × 而不是垃圾桶：这一步只是把它从本场景的序列里移出，
                      配置档本身还在「厂商」页里，垃圾桶会让人以为删掉了那条配置。 */}
                  <IconButton
                    size="sm"
                    label={t('apiConfig:scenarioRemove')}
                    icon={<X size={14} />}
                    onClick={() => void persist(routes.filter((r) => r.profile_id !== route.profile_id))}
                  />
                  <Toggle
                    size="sm"
                    checked={route.enabled}
                    onChange={(v) => void persist(
                      routes.map((r) => (r.profile_id === route.profile_id ? { ...r, enabled: v } : r)),
                    )}
                    ariaLabel={route.provider_name + ' · ' + route.profile_name}
                  />
                </div>
              </div>
            ))
          )}

          <div className="flex items-center gap-2 px-1 pt-1">
            <Button
              variant="ghost"
              size="sm"
              disabled={addable.length === 0}
              onClick={() => setPicking(true)}
            >
              {t('apiConfig:scenarioAdd')}
            </Button>
            {/* 只有「有序列表里全关了」才提示这句；序列为空时上面已经有「还没有配置」，
                两句同时出现会自相矛盾。 */}
            {routes.length > 0 && enabledRoutes.length === 0 && scenario.enabled && (
              <span className="text-3xs text-textMuted">{t('apiConfig:scenarioAllOff')}</span>
            )}
          </div>
        </div>
      </AutoHeight>

      {picking && (
        <Dialog onClose={() => setPicking(false)} className="flex items-center justify-center">
          <div
            className="bg-elevated border border-border rounded-dialog p-4 w-full max-w-sm mx-4 shadow-2xl shadow-black/30"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-base font-semibold text-textPrimary">{t('apiConfig:scenarioPickTitle')}</h2>
            <div className="mt-3 space-y-1">
              {addable.map((profile) => (
                <Button
                  key={profile.id}
                  variant="ghost"
                  size="sm"
                  className="w-full !justify-start"
                  onClick={() => {
                    setPicking(false)
                    void persist([
                      ...routes,
                      {
                        profile_id: profile.id,
                        profile_name: profile.label,
                        provider_name: '',
                        position: routes.length + 1,
                        enabled: true,
                        status: null,
                      },
                    ])
                  }}
                >
                  {profile.label}
                </Button>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <Button variant="ghost" size="sm" onClick={() => setPicking(false)}>
                {t('apiConfig:cancel')}
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  )
}
