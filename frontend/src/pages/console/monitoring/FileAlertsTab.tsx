// 文件告警：把所有「文件额度/清理」相关的系统消息单开一页
//
// 为什么不只靠通用日志页筛：这三类事件一天最多各一条（见 file_budget 的 _NOTIFIED 节流），
// 它们不是流水，是「今天有没有出事」的仪表。所以要有一页把「是什么、谁撞的、去哪改」放在
// 同一屏里；通用日志页是给翻历史用的。

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { Card, Button } from '../../../components/ui'

const KINDS = [
  { key: 'file_budget_exceeded', countKey: 'budget', labelKey: 'admin:fileAlertsBudget', hintKey: 'admin:fileAlertsBudgetHint' },
  { key: 'file_quota_exceeded', countKey: 'quota', labelKey: 'admin:fileAlertsQuota', hintKey: 'admin:fileAlertsQuotaHint' },
  { key: 'file_cleanup_failed', countKey: 'cleanup_failed', labelKey: 'admin:fileAlertsCleanup', hintKey: 'admin:fileAlertsCleanupHint' },
] as const

type Row = { id: number; log_type: string; created_at: string; operator_type: string; operator_id: number; details: any }

function account(row: Row): string {
  return row.operator_type === 'system' ? 'system' : `${row.operator_type}:${row.operator_id}`
}

export default function FileAlertsTab() {
  const t = useT()
  const navigate = useNavigate()
  const [rows, setRows] = useState<Row[] | null>(null)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [err, setErr] = useState('')

  useEffect(() => {
    Promise.all(KINDS.map(k => api.get(`/admin/logs?page_size=20&log_type=${k.key}`).catch(() => ({ items: [] }))))
      .then((results: any[]) => {
        const merged: Row[] = []
        results.forEach((r) => merged.push(...((r?.items || []) as Row[])))
        merged.sort((x, y) => String(y.created_at).localeCompare(String(x.created_at)))
        setRows(merged)
      })
      .catch((e: any) => setErr(String(e?.message || e)))
    // 卡片上的数字只认总览那一处口径（近 N 天累计，含被节流掉的那些）：列表只是样本，
    // 拿「本次拉到的条数」当计数，会把「今天一条都没有」和「还没拉到」混成同一个数
    api.get('/admin/ops/overview').then((d: any) => {
      if (d?.files) setCounts(d.files)
    }).catch(() => {})
  }, [])

  if (err) return <p className="text-rose-400">{err}</p>
  if (!rows) return <p className="text-textMuted">{t('common:loading')}</p>

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        {KINDS.map(k => (
          <Card
            key={k.key}
            title={t(k.labelKey)}
            hint={t(k.hintKey)}
            action={
              <Button variant="outline" size="xs" onClick={() => navigate({ search: 'tab=runtime' })}>
                {t('admin:fileAlertsGoSettings')}
              </Button>
            }
          >
            <p className="text-2xl font-semibold text-textPrimary">{counts[k.countKey] ?? '—'}</p>
          </Card>
        ))}
      </div>

      <p className="text-xs text-textMuted">{t('admin:fileAlertsExplain')}</p>

      {rows.length === 0 ? (
        <p className="text-sm text-textMuted">{t('admin:fileAlertsEmpty')}</p>
      ) : (
        <div className="overflow-auto rounded-card border border-border/60">
          <table className="w-full min-w-[720px] text-sm text-textPrimary">
            <thead className="bg-surface">
              <tr className="border-b border-border">
                <th className="text-left py-2 px-3 font-medium text-textSecondary whitespace-nowrap">{t('admin:logsColTime')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin:logsColType')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin:logsColOperator')}</th>
                <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin:logsColDetail')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const kind = KINDS.find(k => k.key === row.log_type)
                return (
                  <tr key={row.id} className="border-b border-border/40 last:border-0">
                    <td className="py-2 px-3 whitespace-nowrap text-textMuted">{String(row.created_at).replace('T', ' ').slice(0, 19)}</td>
                    <td className="py-2 px-3">{kind ? t(kind.labelKey) : row.log_type}</td>
                    <td className="py-2 px-3 whitespace-nowrap">{account(row)}</td>
                    <td className="py-2 px-3 text-xs text-textMuted">
                      <pre className="whitespace-pre-wrap break-all">{row.details ? JSON.stringify(row.details, null, 2) : '-'}</pre>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
