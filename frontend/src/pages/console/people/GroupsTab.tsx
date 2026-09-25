// 群聊审查

import { useState, useEffect } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'

export default function GroupsTab() {
  const t = useT()
  const [data, setData] = useState<any>(null)
  useEffect(() => {
    api.get('/admin/groups').then(setData).catch(console.error)
  }, [])

  if (!data) return <p className="text-textMuted">{t('common.loading')}</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-textPrimary">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.groupsColId')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.groupsColName')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.groupsColOwner')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.groupsColVector')}</th>
            <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.groupsColAction')}</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((g: any) => (
            <tr key={g.id} className="border-b border-border/50">
              <td className="py-2 px-3">{g.id}</td>
              <td className="py-2 px-3 font-medium">{g.name}</td>
              <td className="py-2 px-3">{g.owner_type}:{g.owner_id}</td>
              <td className="py-2 px-3">
                {g.is_vector_accelerated ? t('admin.enabled') : t('admin.notEnabled')}
              </td>
              <td className="py-2 px-3">
                <button
                  onClick={async () => {
                    if (confirm(t('admin.confirmDismissGroup'))) {
                      await api.delete(`/admin/groups/${g.id}`)
                      const newData = await api.get('/admin/groups')
                      setData(newData)
                    }
                  }}
                  className="text-xs text-rose-400 hover:text-rose-500 dark:hover:text-rose-300"
                >
                  {t('admin.dismiss')}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
