// AI 角色管理

import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'

export default function AgentsTab() {
  const t = useT()
  const [data, setData] = useState<any>(null)
  useEffect(() => {
    api.get('/admin/agents').then(setData).catch(console.error)
  }, [])

  if (!data) return <p className="text-textMuted">{t('common.loading')}</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-textPrimary">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.agentsColId')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.agentsColName')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.agentsColOwner')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.agentsColStatus')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.agentsColSelfEdit')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.agentsColAction')}</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((a: any) => (
            <tr key={a.id} className="border-b border-border/50">
              <td className="py-2 px-3">{a.id}</td>
              <td className="py-2 px-3 font-medium">{a.name}</td>
              <td className="py-2 px-3">{a.owner_id}</td>
              <td className="py-2 px-3">{a.state}</td>
              <td className="py-2 px-3">
                <span className={a.is_ai_editable ? 'text-mint-400' : 'text-rose-400'}>
                  {a.is_ai_editable ? t('common.yes') : t('common.no')}
                </span>
              </td>
              <td className="py-2 px-3">
                <button
                  onClick={async () => {
                    await api.put(`/admin/agents/${a.id}/editable`, { is_ai_editable: !a.is_ai_editable })
                    // 触发刷新
                    const newData = await api.get('/admin/agents')
                    setData(newData)
                  }}
                  className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300"
                >
                  {a.is_ai_editable ? t('admin.disableSelfEdit') : t('admin.enableSelfEdit')}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
