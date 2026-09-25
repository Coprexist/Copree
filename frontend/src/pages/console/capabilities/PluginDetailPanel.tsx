import type { ReactNode } from 'react'
import { Play, Square, X } from 'lucide-react'
import { api } from '../../../api/client'
import { Badge, Button, Dialog, IconButton } from '../../../components/ui'
import { useT } from '../../../i18n/I18nContext'
import PluginServiceInstances from './PluginServiceInstances'
import type { PluginView } from '../../../utils/skin'

/** 列表里的一个条目：目录插件 / 内置服务 / 前端声明的内置能力，三种共用同一份详情 */
export interface BuiltinService {
  id: string
  name: string
  description: string
  installed: boolean
  running: boolean
  port: number | null
  owner?: string | null
}

export interface PluginEntry {
  key: string
  kind: 'dir' | 'service' | 'capability'
  id: string
  name: string
  description: string
  category: string
  categoryLabel: string
  version: string
  /** 作者（目录插件有；内置能力为空） */
  author?: string
  builtin: boolean
  icon: ReactNode
  plugin?: PluginView
  service?: BuiltinService
  render?: () => ReactNode
}

/**
 * 插件详情 —— 右侧抽屉
 *
 * 为什么不是"就地展开在列表行里"：行内展开会把列表撑高、挤动其它行（用户实测反馈），
 * 而且配置里还有表单和多实例，塞进一个表格单元格里既挤又打断阅读。
 * 为什么不是弹窗/悬窗：详情里要填表单、传凭据，悬窗一移开鼠标就没了；
 * 弹窗遮全屏，看不到"我在改的是列表里的哪一个"。
 * 抽屉两者都避开：列表留在左边当上下文，操作区在右边，站内也已有抽屉（移动端侧栏）与 z-drawer 令牌。
 */
export default function PluginDetailPanel({ entry, onClose, onReload, onMessage }: {
  entry: PluginEntry
  onClose: () => void
  onReload: () => void | Promise<void>
  onMessage: (msg: { type: 'success' | 'error'; text: string }) => void
}) {
  const t = useT()

  const toggleBuiltin = async (service: BuiltinService) => {
    try {
      const res: any = await api.post(`/admin/plugins/${service.id}/${service.running ? 'stop' : 'start'}`)
      onMessage({ type: 'success', text: res.message || '' })
      await onReload()
    } catch (e: any) {
      onMessage({ type: 'error', text: `${e?.message || e}` })
    }
  }

  const testBrowser = async () => {
    try {
      const res: any = await api.post('/admin/plugins/browser/test')
      const detail = res.cdp_msgs?.length ? ` [CDP: ${res.cdp_msgs.join('→')}]` : ''
      onMessage({ type: res.ok ? 'success' : 'error', text: (res.ok ? `${res.message}` : `${res.error}`) + detail })
    } catch (e: any) {
      onMessage({ type: 'error', text: `${e?.message || e}` })
    }
  }

  return (
    <Dialog layer="drawer" className="flex justify-end" backdrop="bg-black/40" onClose={onClose}>
      <div className="h-full w-full max-w-xl bg-surface border-l border-border shadow-2xl overflow-y-auto">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-3 px-4 py-3 border-b border-border bg-surface">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-textMuted shrink-0">{entry.icon}</span>
              <h3 className="font-medium text-textPrimary truncate">{entry.name}</h3>
              <Badge tone="muted">{entry.categoryLabel}</Badge>
              {entry.version && <span className="text-3xs font-mono text-textMuted">v{entry.version}</span>}
              {entry.builtin && <Badge tone="primary">{t('tool:plugin.builtin')}</Badge>}
            </div>
          </div>
          <IconButton icon={<X size={16} />} label={t('tool:plugin.close')} onClick={onClose} />
        </header>

        <div className="p-4 space-y-4">
          {/* 列表里只放一行截断的说明（认出它）；完整说明住在这里（讲清楚它） */}
          <div className="space-y-1">
            <p className="text-xs text-textSecondary leading-relaxed">{entry.description}</p>
            <div className="flex items-center gap-3 flex-wrap text-2xs text-textMuted">
              <span>{t('tool:plugin.metaType')}: {entry.categoryLabel}</span>
              {entry.version && <span>{t('tool:plugin.metaVersion')}: v{entry.version}</span>}
              {entry.author && <span>{t('tool:plugin.metaAuthor')}: {entry.author}</span>}
              <span>{entry.builtin ? t('tool:plugin.builtin') : t('tool:plugin.installedFromDisk')}</span>
            </div>
          </div>

          {entry.kind === 'capability' && entry.render?.()}

          {entry.kind === 'service' && entry.service && (
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone={entry.service.running ? 'mint' : 'muted'}>
                {entry.service.running ? t('tool:plugin.statusRunning1') : t('tool:plugin.statusStopped')}
              </Badge>
              {entry.service.running && entry.service.port && (
                <span className="text-2xs font-mono text-textMuted">:{entry.service.port}</span>
              )}
              <Button
                size="xs"
                variant={entry.service.running ? 'outline' : 'primary'}
                icon={entry.service.running ? <Square size={12} /> : <Play size={12} />}
                onClick={() => toggleBuiltin(entry.service!)}
              >
                {entry.service.running ? t('tool:pluginService.stop') : t('tool:pluginService.start')}
              </Button>
              {entry.id === 'browser' && entry.service.running && (
                <Button size="xs" variant="secondary" onClick={testBrowser}>{t('tool:plugin.testBrowser')}</Button>
              )}
            </div>
          )}

          {entry.kind === 'dir' && entry.plugin?.category === 'service' && entry.plugin.service && (
            <>
              <PluginServiceInstances plugin={entry.plugin} standalone onChanged={onReload} onMessage={onMessage} />
              <p className="text-2xs text-textMuted">{t('tool:plugin.serviceHint')}</p>
            </>
          )}

          {entry.kind === 'dir' && entry.plugin?.category !== 'service' && entry.plugin && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge tone={entry.plugin.global_enabled ? 'mint' : 'muted'}>
                  {entry.plugin.global_enabled ? t('tool:plugin.globalOn') : t('tool:plugin.globalOff')}
                </Badge>
                {entry.plugin.users_count != null && (
                  <span className="text-2xs text-textMuted">{t('tool:plugin.usersUsing', { n: String(entry.plugin.users_count) })}</span>
                )}
              </div>
              <p className="text-2xs text-textMuted">{t('tool:plugin.dirHint')}</p>
            </div>
          )}
        </div>
      </div>
    </Dialog>
  )
}
