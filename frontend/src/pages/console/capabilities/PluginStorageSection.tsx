// 插件数据：插件自己申请的表留了什么，卸载后保留的也在这里
//
// 为什么单独一块而不是塞进插件卡片：卸载之后插件已经不在列表里了，而它的数据还在。
// 「删了代码但数据还在」必须是可见、可清的，否则就成了没人知道的黑账。

import { useCallback, useEffect, useState } from 'react'
import { Database, Loader2, Trash2 } from 'lucide-react'

import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { Button, Card, confirmAsync } from '../../../components/ui'

interface AssetTable {
  table: string
  rows: number
  bytes: number | null
}

interface AssetItem {
  plugin_id: string
  installed: boolean
  tables: AssetTable[]
  rows: number
  bytes: number
  error?: string | null
}

function fmtBytes(n: number): string {
  if (!n) return '—'
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}

export default function PluginStorageSection() {
  const t = useT()
  const [items, setItems] = useState<AssetItem[]>([])
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ items: AssetItem[] }>('/plugins/storage')
      setItems(data.items || [])
    } catch {
      setItems([])          // 非管理员/接口不可用时不画这块，别弹错
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const purge = async (item: AssetItem) => {
    const ok = await confirmAsync({
      title: t('tool:storage.purge'),
      message: t('tool:storage.purgeConfirm', { id: item.plugin_id, n: item.tables.length }),
      danger: true,
      confirmText: t('tool:storage.purge'),
    })
    if (!ok) return
    setBusy(item.plugin_id)
    setMsg('')
    try {
      await api.post('/plugins/storage/' + encodeURIComponent(item.plugin_id) + '/purge')
      setMsg(t('tool:storage.purged', { id: item.plugin_id }))
      await load()
    } catch (e: any) {
      setMsg(e?.detail || e?.message || String(e))
    } finally {
      setBusy('')
    }
  }

  if (items.length === 0) return null

  return (
    <Card title={t('tool:storage.title')} icon={<Database size={14} className="text-primary-400" />}>
      <p className="text-3xs text-textMuted mb-2">{t('tool:storage.hint')}</p>
      <div className="overflow-auto max-h-[50vh] rounded-card border border-border">
        <table className="w-full min-w-[640px] text-xs">
          <thead className="sticky top-0 z-10 bg-surface">
            <tr className="border-b border-border text-textSecondary">
              <th className="text-left py-2 px-3 font-medium">{t('tool:storage.plugin')}</th>
              <th className="text-left py-2 px-3 font-medium">{t('tool:storage.state')}</th>
              <th className="text-right py-2 px-3 font-medium">{t('tool:storage.tables')}</th>
              <th className="text-right py-2 px-3 font-medium">{t('tool:storage.rows')}</th>
              <th className="text-right py-2 px-3 font-medium">{t('tool:storage.size')}</th>
              <th className="text-right py-2 px-3 font-medium">{t('tool:storage.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.plugin_id} className="border-b border-border/50 last:border-0 align-top">
                <td className="py-2 px-3 font-mono text-textPrimary break-all">{item.plugin_id}</td>
                <td className="py-2 px-3 whitespace-nowrap">
                  <span className={item.installed ? 'text-mint-400' : 'text-accent-400'}>
                    {t(item.installed ? 'tool:storage.installed' : 'tool:storage.removed')}
                  </span>
                  {item.error && (
                    <div className="text-3xs text-rose-400 mt-1 break-words">{item.error}</div>
                  )}
                </td>
                <td className="py-2 px-3 text-right tabular-nums text-textSecondary">{item.tables.length}</td>
                <td className="py-2 px-3 text-right tabular-nums text-textSecondary">{item.rows}</td>
                <td className="py-2 px-3 text-right tabular-nums text-textSecondary">{fmtBytes(item.bytes)}</td>
                <td className="py-2 px-3 text-right whitespace-nowrap">
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={item.tables.length === 0 || busy === item.plugin_id}
                    onClick={() => purge(item)}
                  >
                    {busy === item.plugin_id
                      ? <Loader2 size={11} className="animate-spin" />
                      : <Trash2 size={11} />}
                    <span className="ml-1">{t('tool:storage.purge')}</span>
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!!msg && <p className="text-3xs text-textSecondary mt-2 break-words">{msg}</p>}
    </Card>
  )
}
