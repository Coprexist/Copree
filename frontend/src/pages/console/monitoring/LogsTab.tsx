// 审计日志

import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'

const GEOIP_CACHE_KEY = '_geoip_cache'

export default function LogsTab() {
  const t = useT()
  const [data, setData] = useState<any>(null)
  const [geoip, setGeoip] = useState<Record<string, any>>(() => {
    try { return JSON.parse(sessionStorage.getItem(GEOIP_CACHE_KEY) || '{}') } catch { return {} }
  })

  useEffect(() => {
    api.get('/admin/logs?page_size=50').then((d) => {
      setData(d)
      const ips: string[] = [...new Set<string>((d.items || []).map((l: any) => String(l.ip_address)).filter(Boolean))]
      const uncached = ips.filter(ip => !geoip[ip])
      if (uncached.length > 0) {
        api.post('/admin/geoip/resolve', { ips: uncached }).then(res => {
          if (res?.results) {
            const merged = { ...geoip, ...res.results }
            setGeoip(merged)
            sessionStorage.setItem(GEOIP_CACHE_KEY, JSON.stringify(merged))
          }
        }).catch(() => {})
      }
    }).catch(console.error)
  }, [])

  if (!data) return <p className="text-textMuted">{t('common.loading')}</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-textPrimary">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColTime')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColType')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColOperator')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColTarget')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">IP</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColDetail')}</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((log: any) => {
            const loc = log.ip_address ? geoip[log.ip_address] : null
            return (
              <tr key={log.id} className="border-b border-border/50">
                <td className="py-2 px-3 text-xs">
                  {log.created_at ? new Date(log.created_at).toLocaleString('zh-CN') : '-'}
                </td>
                <td className="py-2 px-3">
                  <span className="text-xs px-2 py-0.5 rounded bg-elevated text-textPrimary">{log.log_type}</span>
                </td>
                <td className="py-2 px-3">{log.operator_type}:{log.operator_id}</td>
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
            )}
          )}
        </tbody>
      </table>
    </div>
  )
}


// ============================================================
// OpenCLI 管理 Tab
// ============================================================
