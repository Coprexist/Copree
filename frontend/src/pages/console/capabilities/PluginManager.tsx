import { useState, useEffect } from 'react'
import {
  BookOpen, Box, FileDown, Globe, Link2, Palette, Plug, RefreshCw, RefreshCcw, Server, Wand2,
} from 'lucide-react'
import { api } from '../../../api/client'
import { Button, EmptyState, Input, ListPanel, Select } from '../../../components/ui'
import { useT } from '../../../i18n/I18nContext'
import DocExportTab from './DocExportTab'
import ApiDocSectionsTab from './ApiDocSectionsTab'
import DshBridgeTab from './DshBridgeTab'
import PluginDetailPanel, { type PluginEntry } from './PluginDetailPanel'
import Toggle from '../../../components/Toggle'
import type { PluginView } from '../../../utils/skin'
import { CATEGORY_ICON, CATEGORY_LABEL_KEY } from '../../../utils/pluginCategories'

interface Plugin {
  id: string
  name: string
  description: string
  category: string
  installed: boolean
  running: boolean
  port: number | null
  owner?: string | null
}

type Tone = 'mint' | 'muted' | 'accent' | 'rose'

const STATUS_CLASS: Record<Tone, string> = {
  mint: 'text-mint-400',
  muted: 'text-textMuted',
  accent: 'text-amber-600 dark:text-amber-400',
  rose: 'text-rose-400',
}

/** 状态点：一眼看出"在不在跑"，文字紧跟其后（Chrome 扩展页/VS Code 都是这个组合） */
const DOT_CLASS: Record<Tone, string> = {
  mint: 'bg-mint-400',
  muted: 'bg-gray-400',
  accent: 'bg-amber-400',
  rose: 'bg-rose-400',
}

/** 目录插件 → 一句话状态（细节进详情，列表不摊） */
function dirStatus(plugin: PluginView, t: (k: string, v?: Record<string, string>) => string): { tone: Tone; text: string } {
  if (!plugin.global_enabled) return { tone: 'muted', text: t('tool:plugin.globalOff') }
  const svc = plugin.service
  if (plugin.category === 'service' && svc) {
    const total = svc.instances.length
    if (total === 0) return { tone: 'accent', text: t('tool:plugin.statusNoInstance') }
    if (svc.instances.some(i => i.missing_required.length > 0)) {
      return { tone: 'accent', text: t('tool:plugin.statusNeedConfig') }
    }
    const running = svc.instances.filter(i => i.running).length
    return running > 0
      ? { tone: 'mint', text: t('tool:plugin.statusRunning', { running: String(running), total: String(total) }) }
      : { tone: 'muted', text: t('tool:plugin.statusStoppedAll', { total: String(total) }) }
  }
  return { tone: 'mint', text: t('tool:plugin.globalOn') }
}

/** 读接口带一次自动重试：后端重启/网络抖一下时，页面不至于一直停在"加载失败"上 */
async function getWithRetry<T>(path: string): Promise<T> {
  try {
    return await api.get<T>(path)
  } catch (e) {
    await new Promise(resolve => setTimeout(resolve, 1200))
    return await api.get<T>(path)
  }
}

export default function PluginManager() {
  const t = useT()
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const [contentPlugins, setContentPlugins] = useState<PluginView[]>([])
  const [contentLoading, setContentLoading] = useState(false)
  const [contentToggling, setContentToggling] = useState<string | null>(null)
  const [builtins, setBuiltins] = useState<Plugin[]>([])
  const [builtinToggling, setBuiltinToggling] = useState<string | null>(null)

  const [q, setQ] = useState('')
  const [category, setCategory] = useState('all')
  const [openKey, setOpenKey] = useState<string | null>(null)

  const fetchContentPlugins = async () => {
    setContentLoading(true)
    try {
      const data = await getWithRetry<{ plugins: PluginView[] }>('/plugins')
      setContentPlugins(data.plugins || [])
    } catch (e: any) {
      setMessage({ type: 'error', text: `${t('tool:plugin.loadFailed')}: ${e?.message || e}` })
    } finally {
      setContentLoading(false)
    }
  }

  const fetchBuiltins = async () => {
    try {
      const data: any = await getWithRetry('/admin/plugins')
      setBuiltins(data.plugins || [])
    } catch (e: any) {
      setMessage({ type: 'error', text: `${t('tool:plugin.loadFailed')}: ${e?.message || e}` })
    }
  }

  useEffect(() => { fetchContentPlugins(); fetchBuiltins() }, [])

  // 内置服务有运行态，轮询刷新（目录插件的运行态跟着实例列表走）
  useEffect(() => {
    const iv = setInterval(fetchBuiltins, 5000)
    return () => clearInterval(iv)
  }, [])

  const toggleDirPlugin = async (plugin: PluginView) => {
    setContentToggling(plugin.id)
    setMessage(null)
    try {
      const res: any = await api.post(`/plugins/${plugin.id}/toggle`)
      setMessage({ type: 'success', text: res.message || '' })
      await fetchContentPlugins()
    } catch (e: any) {
      setMessage({ type: 'error', text: `${e?.message || e}` })
    } finally {
      setContentToggling(null)
    }
  }

  const toggleBuiltin = async (plugin: Plugin) => {
    setBuiltinToggling(plugin.id)
    setMessage(null)
    try {
      const action = plugin.running ? 'stop' : 'start'
      const res: any = await api.post(`/admin/plugins/${plugin.id}/${action}`)
      setMessage({ type: 'success', text: res.message || '' })
      await fetchBuiltins()
    } catch (e: any) {
      setMessage({ type: 'error', text: `${e?.message || e}` })
    } finally {
      setBuiltinToggling(null)
    }
  }

  const rescan = async () => {
    setContentLoading(true)
    try {
      const res: any = await api.post('/plugins/rescan')
      setMessage({ type: 'success', text: res.message || '' })
      await fetchContentPlugins()
    } catch (e: any) {
      setMessage({ type: 'error', text: `${e?.message || e}` })
    } finally {
      setContentLoading(false)
    }
  }

  // 类别文案走共享 key（utils/pluginCategories）：商城分区用的是同一份，避免两处各写一套
  const label = (key: string) => t(CATEGORY_LABEL_KEY[key] || CATEGORY_LABEL_KEY.other)

  // 三类来源汇总成一张表：目录插件 / 内置服务 / 前端声明的内置能力
  const capabilityEntries: PluginEntry[] = [
    {
      key: 'cap:dsh-bridge', kind: 'capability', id: 'dsh-bridge', category: 'builtin', categoryLabel: label('builtin'),
      version: '', builtin: true, name: t('tool:plugin.capability.dsh'),
      description: t('tool:plugin.capability.dshDesc'), icon: <Link2 size={15} />, render: () => <DshBridgeTab />,
    },
    {
      key: 'cap:doc-export', kind: 'capability', id: 'doc-export', category: 'builtin', categoryLabel: label('builtin'),
      version: '', builtin: true, name: t('tool:plugin.capability.docExport'),
      description: t('tool:plugin.capability.docExportDesc'), icon: <FileDown size={15} />, render: () => <DocExportTab />,
    },
    {
      key: 'cap:api-doc', kind: 'capability', id: 'api-doc', category: 'builtin', categoryLabel: label('builtin'),
      version: '', builtin: true, name: t('tool:plugin.capability.apiDoc'),
      description: t('tool:plugin.capability.apiDocDesc'), icon: <BookOpen size={15} />, render: () => <ApiDocSectionsTab />,
    },
  ]

  const dirEntries: PluginEntry[] = contentPlugins.map(p => ({
    key: `dir:${p.id}`, kind: 'dir', id: p.id, name: p.name, description: p.description,
    category: p.category, categoryLabel: label(p.category), version: p.version, builtin: p.builtin,
    author: p.author,
    icon: (() => { const I = CATEGORY_ICON[p.category] || Box; return <I size={15} /> })(),
    plugin: p,
  }))

  const serviceEntries: PluginEntry[] = builtins.filter(b => !b.owner).map(b => ({
    key: `svc:${b.id}`, kind: 'service', id: b.id, name: b.name, description: b.description,
    category: 'service', categoryLabel: label('service'), version: '', builtin: true,
    icon: <Server size={15} />, service: b,
  }))

  const rows = [...dirEntries, ...serviceEntries, ...capabilityEntries]

  const keyword = q.trim().toLowerCase()
  const filtered = rows.filter(r => {
    if (category !== 'all' && r.category !== category) return false
    if (!keyword) return true
    return `${r.name} ${r.description} ${r.id}`.toLowerCase().includes(keyword)
  })

  const openEntry = rows.find(r => r.key === openKey) || null

  const statusOf = (entry: PluginEntry) => {
    if (entry.kind === 'service' && entry.service) {
      const s = entry.service
      if (!s.installed) return { tone: 'rose' as Tone, text: t('tool:plugin.statusNotInstalled') }
      return s.running
        ? { tone: 'mint' as Tone, text: `${t('tool:plugin.statusRunning1')}${s.port ? ` :${s.port}` : ''}` }
        : { tone: 'muted' as Tone, text: t('tool:plugin.statusStopped') }
    }
    if (entry.kind === 'dir' && entry.plugin) return dirStatus(entry.plugin, t)
    return { tone: 'muted' as Tone, text: t('tool:plugin.statusBuiltin') }
  }

  return (
    <div className="space-y-4">
      {message && (
        <div className={`p-3 rounded-control text-sm ${
          message.type === 'success'
            ? 'bg-mint-50 dark:bg-mint-900/20 text-mint-700 dark:text-mint-400 border border-mint-200 dark:border-mint-800'
            : 'bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-400 border border-rose-200 dark:border-rose-800'
        }`}>
          <div className="flex items-center gap-3">
            <span>{message.text}</span>
            {/* 网络抖一下（后端重启/断网）时给个重试，别让人只能刷页面 */}
            {message.type === 'error' && (
              <button
                onClick={async () => { setMessage(null); await fetchContentPlugins(); await fetchBuiltins() }}
                className="text-xs underline hover:no-underline shrink-0"
              >
                {t('tool:plugin.retry')}
              </button>
            )}
          </div>
        </div>
      )}

      <ListPanel
        columns={[
          // 说明并入第一列（名称下面一行）：列表负责"认出它"，详情负责"讲清楚它"
          // w-px = "按内容给最小宽度、别撑开"，把富余宽度全留给插件列，状态/操作就不会被挤成竖排
          { key: 'name', label: t('tool:plugin.colName') },
          { key: 'status', label: t('tool:plugin.colStatus'), className: 'w-px whitespace-nowrap' },
          { key: 'action', label: t('tool:plugin.colAction'), className: 'w-px whitespace-nowrap' },
        ]}
        toolbar={
          <>
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t('tool:plugin.search')}
              fieldSize="sm"
              className="w-48"
            />
            <Select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              options={[
                { value: 'all', label: t('tool:plugin.filterAll') },
                { value: 'service', label: label('service') },
                { value: 'skin', label: label('skin') },
                { value: 'skill', label: label('skill') },
                { value: 'world', label: label('world') },
                { value: 'builtin', label: label('builtin') },
                { value: 'other', label: label('other') },
              ]}
              fieldSize="sm"
            />
            <span className="text-2xs text-textMuted">{t('tool:plugin.count', { n: String(filtered.length) })}</span>
            <span className="flex-1" />
            <Button size="sm" variant="secondary" icon={<RefreshCcw size={14} />} loading={contentLoading} onClick={rescan}>
              {t('tool:plugin.rescan')}
            </Button>
          </>
        }
        empty={filtered.length === 0 ? (
          <EmptyState
            icon={contentLoading ? RefreshCw : Plug}
            title={contentLoading ? t('tool:plugin.loading') : t('tool:plugin.empty')}
            description={keyword || category !== 'all' ? t('tool:plugin.emptyFiltered') : undefined}
          />
        ) : undefined}
      >
        {filtered.map(entry => {
          const status = statusOf(entry)
          return (
            <tr key={entry.key} className="border-b border-border/50">
                <td className="py-2 px-3">
                  <div className="flex items-start gap-2.5">
                    <span className="text-textMuted shrink-0 mt-0.5">{entry.icon}</span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 whitespace-nowrap">
                        <span className="font-medium text-textPrimary">{entry.name}</span>
                        <span className="text-3xs text-textMuted">{entry.categoryLabel}</span>
                        {entry.version && <span className="text-3xs font-mono text-textMuted">v{entry.version}</span>}
                        {entry.builtin && <span className="text-3xs text-textMuted">{t('tool:plugin.builtin')}</span>}
                      </div>
                      {/* 只留一行，鼠标悬停给全文（截断必须留一条看全文的路） */}
                      <p className="text-xs text-textSecondary truncate" title={entry.description}>{entry.description}</p>
                    </div>
                  </div>
                </td>
                <td className="py-2 px-3 whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${DOT_CLASS[status.tone]}`} />
                    <span className={`text-xs ${STATUS_CLASS[status.tone]}`}>{status.text}</span>
                  </span>
                </td>
                <td className="py-2 px-3 whitespace-nowrap">
                  <div className="flex items-center justify-end gap-3">
                    {/* 开关用 Switch 而不是会变字的文字链接：大厂的"启用/停用"都是同一枚控件，位置固定、状态靠形态表达 */}
                    {entry.kind === 'dir' && entry.plugin && (
                      <Toggle
                        size="sm"
                        checked={entry.plugin.global_enabled}
                        disabled={contentToggling === entry.id}
                        ariaLabel={entry.name}
                        onChange={() => toggleDirPlugin(entry.plugin!)}
                      />
                    )}
                    {entry.kind === 'service' && entry.service?.installed && (
                      <Toggle
                        size="sm"
                        checked={entry.service.running}
                        disabled={builtinToggling === entry.id}
                        ariaLabel={entry.name}
                        onChange={() => toggleBuiltin(entry.service as Plugin)}
                      />
                    )}
                    <button
                      onClick={() => setOpenKey(entry.key)}
                      className="text-xs text-textSecondary hover:text-textPrimary"
                    >
                      {t('tool:plugin.detail')}
                    </button>
                  </div>
                </td>
              </tr>
          )
        })}
      </ListPanel>

      {/* 详情在右侧抽屉里：列表留在原地当上下文，配置与表单不挤动列表 */}
      {openEntry && (
        <PluginDetailPanel
          entry={openEntry}
          onClose={() => setOpenKey(null)}
          onReload={async () => { await fetchContentPlugins(); await fetchBuiltins() }}
          onMessage={setMessage}
        />
      )}
    </div>
  )
}
