// 审计日志

import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { useT, useLang } from '../../../i18n/I18nContext'
import { fmtDateTime } from '../../../utils/time'

const GEOIP_CACHE_KEY = '_geoip_cache'

// 已知类型给 i18n 标签，其余原样显示：新增 log_type 不该因为前端没同步就变成空白
const TYPE_LABELS: Record<string, string> = {
  login: 'admin:logsTypeLogin',
  login_failed: 'admin:logsTypeLoginFailed',
  register: 'admin:logsTypeRegister',
  file_budget_exceeded: 'admin:logsTypeBudget',
  file_quota_exceeded: 'admin:logsTypeQuota',
  file_cleanup_failed: 'admin:logsTypeCleanup',
}

const FILTER_TYPES = ['all', 'login', 'login_failed', 'register',
  'file_budget_exceeded', 'file_quota_exceeded', 'file_cleanup_failed']

/** 详情列的内容：JSON 就缩进成多行再显示。
 *  一行 JSON 折行后读不出结构，而这一列本来就是给人看的排查线索。 */
function formatDetail(details: any): string {
  if (details === null || details === undefined) return '-'
  const pretty = (value: any) => {
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return String(value)
    }
  }
  if (typeof details === 'string') {
    try {
      return pretty(JSON.parse(details))
    } catch {
      return details
    }
  }
  return pretty(details)
}

export default function LogsTab() {
  const t = useT()
  const lang = useLang()
  const [data, setData] = useState<any>(null)
  // 运维总览的数字点进来时带着 log_type：URL 是下钻落点的唯一入口，刷新/收藏也还在
  const [searchParams] = useSearchParams()
  const fromUrl = searchParams.get('log_type') || ''
  const [filterType, setFilterType] = useState(FILTER_TYPES.includes(fromUrl) ? fromUrl : 'all')
  const [geoip, setGeoip] = useState<Record<string, any>>(() => {
    try { return JSON.parse(sessionStorage.getItem(GEOIP_CACHE_KEY) || '{}') } catch { return {} }
  })
  // 缓存另存一份 ref：切换筛选会重跑下面的 effect，从 state 读会拿到上一次渲染的闭包
  const cacheRef = useRef(geoip)

  useEffect(() => {
    const filter = filterType === 'all' ? '' : `&log_type=${encodeURIComponent(filterType)}`
    api.get(`/admin/logs?page_size=50${filter}`).then((d) => {
      setData(d)
      const ips: string[] = [...new Set<string>((d.items || []).map((l: any) => String(l.ip_address)).filter(Boolean))]
      const uncached = ips.filter(ip => !cacheRef.current[ip])
      if (uncached.length > 0) {
        api.post('/admin/geoip/resolve', { ips: uncached }).then(res => {
          if (res?.results) {
            cacheRef.current = { ...cacheRef.current, ...res.results }
            setGeoip(cacheRef.current)
            sessionStorage.setItem(GEOIP_CACHE_KEY, JSON.stringify(cacheRef.current))
          }
        }).catch(() => {})
      }
    }).catch(console.error)
  }, [filterType])

  // 失败要一眼看得出来：撞库找的就是这类行，混在成功的流水里等于没有
  const typeLabel = (type: string) => (TYPE_LABELS[type] ? t(TYPE_LABELS[type]) : type)

  // 首帧 data 还是 null：这里必须先挡住，否则表体那句 data.items 会让整页被错误边界接管
  if (!data) return <p className="text-textMuted">{t('common:loading')}</p>

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-textSecondary">{t('admin:logsFilterType')}:</label>
          <select
            value={filterType}
            onChange={e => setFilterType(e.target.value)}
            className="field field-inline"
          >
            {FILTER_TYPES.map(v => (
              <option key={v} value={v}>
                {v === 'all' ? t('admin:logsFilterAll') : typeLabel(v)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {/* 横向 + 纵向都在容器内滚动：表格给最小宽度，否则 w-full 会把列一直压缩
          （IP 列就是这样被裁掉的，而不是"能滑过去"）；表头吸顶、时间列固定在左侧，
          横向滑到 IP 时仍然知道这一行是什么时候的 */}
      <div className="max-h-[60vh] overflow-auto rounded-card border border-border/60">
        <table className="w-full min-w-[980px] text-sm text-textPrimary">
          <thead className="sticky top-0 z-20 bg-surface">
            <tr className="border-b border-border">
              <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap sticky left-0 z-10 bg-surface">{t('admin:logsColTime')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin:logsColType')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin:logsColResult')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin:logsColOperator')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin:logsColTarget')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">IP</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin:logsColDetail')}</th>
            </tr>
          </thead>
          <tbody>
            {(data.items || []).map((log: any) => {
              const loc = log.ip_address ? geoip[log.ip_address] : null
              const failed = log.success === false
              return (
                <tr key={log.id} className="border-b border-border/50">
                  <td className="py-2 px-3 text-xs whitespace-nowrap tabular-nums sticky left-0 z-10 bg-surface">
                    {log.created_at ? fmtDateTime(log.created_at, lang) : '-'}
                  </td>
                  <td className="py-2 px-3 whitespace-nowrap">
                    <span className={`text-xs px-2 py-0.5 rounded bg-elevated ${failed ? 'text-red-400' : 'text-textPrimary'}`}>
                      {typeLabel(log.log_type)}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-xs">
                    <span className={failed ? 'text-red-400' : 'text-textMuted'}>
                      {failed ? t('admin:logsResultFailed') : t('admin:logsResultOk')}
                    </span>
                    {log.error_message && (
                      <div className="text-textMuted whitespace-normal break-words max-w-[240px]">
                        {log.error_message}
                      </div>
                    )}
                  </td>
                  <td className="py-2 px-3 whitespace-nowrap">
                    {log.operator_id === 0 ? t('admin:logsAnonymous') : `${log.operator_type}:${log.operator_id}`}
                  </td>
                  <td className="py-2 px-3 whitespace-nowrap">{log.target_type}:{log.target_id}</td>
                  <td className="py-2 px-3 whitespace-nowrap">
                    <span className="text-xs text-textMuted font-mono">{log.ip_address || '-'}</span>
                    {loc && (loc.city || loc.country) && (
                      <>
                        <br />
                        <span className="text-3xs text-textMuted" title={loc.isp || ''}>
                          {[loc.city, loc.country].filter(Boolean).join(', ')}
                        </span>
                      </>
                    )}
                  </td>
                  {/* 表格已经能横向滚动，这里就不该再省略号截断：完整显示、允许换行，
                      只有病态长的载荷才在单元格内再滚（否则一行能把整页撑爆）。
                      align-top 让多行详情与同一行其它列的首行对齐 */}
                  <td className="py-2 px-3 text-xs text-textSecondary align-top">
                    <div className="font-mono text-3xs leading-relaxed whitespace-pre-wrap break-words min-w-[240px] max-w-[460px] max-h-40 overflow-auto">
                      {formatDetail(log.details)}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
