// 审计日志

import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'

const GEOIP_CACHE_KEY = '_geoip_cache'

// 已知类型给 i18n 标签，其余原样显示：新增 log_type 不该因为前端没同步就变成空白
const TYPE_LABELS: Record<string, string> = {
  login: 'admin.logsTypeLogin',
  login_failed: 'admin.logsTypeLoginFailed',
  register: 'admin.logsTypeRegister',
}

const FILTER_TYPES = ['all', 'login', 'login_failed', 'register']

export default function LogsTab() {
  const t = useT()
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
  if (!data) return <p className="text-textMuted">{t('common.loading')}</p>

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-textSecondary">{t('admin.logsFilterType')}:</label>
          <select
            value={filterType}
            onChange={e => setFilterType(e.target.value)}
            className="text-sm border border-border rounded-control px-2.5 py-1.5 bg-surface text-textPrimary"
          >
            {FILTER_TYPES.map(v => (
              <option key={v} value={v}>
                {v === 'all' ? t('admin.logsFilterAll') : typeLabel(v)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-textPrimary">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColTime')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColType')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColResult')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColOperator')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColTarget')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">IP</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColDetail')}</th>
            </tr>
          </thead>
          <tbody>
            {(data.items || []).map((log: any) => {
              const loc = log.ip_address ? geoip[log.ip_address] : null
              const failed = log.success === false
              return (
                <tr key={log.id} className="border-b border-border/50">
                  <td className="py-2 px-3 text-xs">
                    {log.created_at ? new Date(log.created_at).toLocaleString('zh-CN') : '-'}
                  </td>
                  <td className="py-2 px-3">
                    <span className={`text-xs px-2 py-0.5 rounded bg-elevated ${failed ? 'text-red-400' : 'text-textPrimary'}`}>
                      {typeLabel(log.log_type)}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-xs">
                    <span className={failed ? 'text-red-400' : 'text-textMuted'}>
                      {failed ? t('admin.logsResultFailed') : t('admin.logsResultOk')}
                    </span>
                    {log.error_message && (
                      <span className="ml-2 text-textMuted max-w-[220px] inline-block truncate align-bottom"
                        title={log.error_message}>
                        {log.error_message}
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3">
                    {log.operator_id === 0 ? t('admin.logsAnonymous') : `${log.operator_type}:${log.operator_id}`}
                  </td>
                  <td className="py-2 px-3">{log.target_type}:{log.target_id}</td>
                  <td className="py-2 px-3">
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
                  <td className="py-2 px-3 text-xs text-textSecondary max-w-[200px] truncate">
                    {JSON.stringify(log.details)}
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
