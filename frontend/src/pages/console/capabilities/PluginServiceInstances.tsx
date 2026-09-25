import { useState } from 'react'
import { CheckCircle, Circle, Play, Plus, RefreshCw, Settings2, Square, Trash2 } from 'lucide-react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import type { PluginServiceInstance, PluginView } from '../../../utils/skin'

/**
 * service 类插件的实例管理（一个插件多份配置：每个 QQ 机器人一份）
 *
 * 保存语义是"列表即真相"：提交哪些实例就留哪些实例，没提交的会被后端删掉（并停掉）。
 * 机密项永远不下发，所以机密字段只回"已保存/未保存"，留空 = 不改，点「清除」才清。
 */
export default function PluginServiceInstances({ plugin, onChanged, onMessage, standalone = false }: {
  plugin: PluginView
  onChanged: () => void | Promise<void>
  onMessage: (msg: { type: 'success' | 'error'; text: string }) => void
  /** 独立成块（抽屉里）时不画顶部分隔线——隔离线是给"列表行内的区块"用的 */
  standalone?: boolean
}) {
  const t = useT()
  const svc = plugin.service!
  const schema = svc.config_schema || {}

  const [open, setOpen] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, Record<string, string>>>({})
  const [secretDraft, setSecretDraft] = useState<Record<string, Record<string, string>>>({})
  const [cleared, setCleared] = useState<Record<string, boolean>>({})
  const [added, setAdded] = useState<string[]>([])
  const [removed, setRemoved] = useState<string[]>([])
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const isSecret = (key: string) => !!schema[key]?.secret
  const blank: PluginServiceInstance = {
    instance: '', values: {}, secrets: {}, running: false, missing_required: [], detail: {}, desired_running: true,
  }
  const list: PluginServiceInstance[] = [
    ...svc.instances.filter(i => !removed.includes(i.instance)),
    ...added.map(name => ({ ...blank, instance: name })),
  ]
  const dirty = Object.keys(draft).length > 0 || Object.keys(secretDraft).length > 0
    || Object.keys(cleared).length > 0 || added.length > 0 || removed.length > 0

  const setValue = (instance: string, key: string, value: string) =>
    setDraft(prev => ({ ...prev, [instance]: { ...(prev[instance] || {}), [key]: value } }))
  const setSecret = (instance: string, key: string, value: string) =>
    setSecretDraft(prev => ({ ...prev, [instance]: { ...(prev[instance] || {}), [key]: value } }))

  const toggleRun = async (inst: PluginServiceInstance) => {
    const key = inst.instance ? `${plugin.id}:${inst.instance}` : plugin.id
    setBusy(inst.instance)
    try {
      const res: any = await api.post(`/admin/plugins/${key}/${inst.running ? 'stop' : 'start'}`)
      onMessage({ type: 'success', text: res.message || '' })
      await onChanged()
    } catch (e: any) {
      onMessage({ type: 'error', text: `${e?.message || e}` })
    } finally {
      setBusy(null)
    }
  }

  const addInstance = () => {
    const name = newName.trim()
    if (!/^[A-Za-z0-9_.-]{1,40}$/.test(name)) {
      onMessage({ type: 'error', text: t('tool:pluginService.instanceNameHint') })
      return
    }
    if (list.some(i => i.instance === name)) {
      onMessage({ type: 'error', text: t('tool:pluginService.instanceExists') })
      return
    }
    setAdded(prev => [...prev, name])
    setNewName('')
    setOpen(name)
  }

  const removeInstance = (instance: string) => {
    if (added.includes(instance)) setAdded(prev => prev.filter(n => n !== instance))
    else setRemoved(prev => [...prev, instance])
    if (open === instance) setOpen(null)
  }

  const save = async () => {
    const payload = list.map(inst => {
      const values: Record<string, string> = { ...(inst.values || {}), ...(draft[inst.instance] || {}) }
      // 机密：只在用户真的输入了才提交（留空 = 保持原值），点了清除就传空串
      for (const [key, value] of Object.entries(secretDraft[inst.instance] || {})) {
        if (value) values[key] = value
      }
      for (const key of Object.keys(cleared)) {
        const [name, field] = [key.slice(0, key.lastIndexOf(':')), key.slice(key.lastIndexOf(':') + 1)]
        if (name === inst.instance && cleared[key]) values[field] = ''
      }
      return { instance: inst.instance, values }
    })
    setSaving(true)
    try {
      const res: any = await api.put(`/plugins/${plugin.id}/config`, { instances: payload })
      onMessage({ type: 'success', text: res.message || t('tool:pluginService.saved') })
      setDraft({}); setSecretDraft({}); setCleared({}); setAdded([]); setRemoved([]); setOpen(null)
      await onChanged()
    } catch (e: any) {
      onMessage({ type: 'error', text: `${t('tool:pluginService.saveFailed')}: ${e?.message || e}` })
    } finally {
      setSaving(false)
    }
  }

  const detailText = (detail: Record<string, any>) =>
    Object.entries(detail || {})
      .filter(([, v]) => v !== null && v !== '' && typeof v !== 'object')
      .slice(0, 4)
      .map(([k, v]) => `${k}: ${v}`)
      .join(' · ')

  const savedKey = (instance: string, key: string) => `${instance}:${key}`

  return (
    <div className={standalone ? 'space-y-2' : 'mt-3 pt-3 border-t border-border space-y-2'}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-medium text-textSecondary">
          {t('tool:pluginService.instances')}（{list.length}）
        </span>
        {dirty && <span className="text-2xs text-amber-600 dark:text-amber-400">{t('tool:pluginService.unsaved')}</span>}
        {dirty && (
          <button
            onClick={save}
            disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-control text-xs font-medium bg-primary-500/10 text-primary-400 hover:bg-primary-500/20 border border-primary-400/20 transition-colors disabled:opacity-50"
          >
            {saving && <RefreshCw size={12} className="animate-spin" />}
            {t('tool:pluginService.save')}
          </button>
        )}
      </div>

      {list.length === 0 && (
        <p className="text-xs text-textMuted">{t('tool:pluginService.noInstance')}</p>
      )}

      {list.map(inst => {
        const missing = svc.instances.find(i => i.instance === inst.instance)?.missing_required || []
        const running = inst.running
        return (
          <div key={inst.instance} className="rounded-control border border-border bg-surface p-3 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-sm text-textPrimary">
                {inst.instance || t('tool:pluginService.defaultInstance')}
              </span>
              <span className={
                running
                  ? 'chip chip-mint shrink-0 dark:bg-mint-900/30 dark:text-mint-400'
                  : 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-2xs font-medium bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400'
              }>
                {running ? t('tool:pluginService.running') : t('tool:pluginService.stopped')}
              </span>
              {missing.length > 0 && (
                <span className="text-2xs text-amber-600 dark:text-amber-400">
                  {t('tool:pluginService.missing')}: {missing.join(' / ')}
                </span>
              )}
              {detailText(inst.detail) && (
                <span className="text-2xs text-textMuted font-mono">{detailText(inst.detail)}</span>
              )}
              <button
                onClick={() => toggleRun(inst)}
                disabled={busy === inst.instance || added.includes(inst.instance)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-control text-xs font-medium transition-colors disabled:opacity-50 ${
                  running
                    ? 'bg-rose-50 dark:bg-rose-900/20 text-rose-600 dark:text-rose-400 hover:bg-rose-100 border border-rose-200 dark:border-rose-800'
                    : 'bg-mint-500 text-white hover:bg-mint-600'
                }`}
              >
                {busy === inst.instance
                  ? <RefreshCw size={12} className="animate-spin" />
                  : running ? <Square size={12} /> : <Play size={12} />}
                {running ? t('tool:pluginService.stop') : t('tool:pluginService.start')}
              </button>
              <button
                onClick={() => setOpen(open === inst.instance ? null : inst.instance)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-control text-xs font-medium bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
              >
                <Settings2 size={12} /> {t('tool:pluginService.config')}
              </button>
              <button
                onClick={() => removeInstance(inst.instance)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-control text-xs font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-900/20 transition-colors"
              >
                <Trash2 size={12} /> {t('tool:pluginService.remove')}
              </button>
            </div>

            {open === inst.instance && (
              <div className="rounded-control border border-border bg-elevated/30 p-3 space-y-2">
                {Object.entries(schema).length === 0 && (
                  <p className="text-xs text-textMuted">{t('tool:pluginService.noConfig')}</p>
                )}
                {Object.entries(schema).map(([key, field]) => {
                  const secret = isSecret(key)
                  const hasSaved = !!inst.secrets[key]
                  const isCleared = !!cleared[savedKey(inst.instance, key)]
                  const value = secret
                    ? (secretDraft[inst.instance]?.[key] ?? '')
                    : (draft[inst.instance]?.[key] ?? inst.values[key] ?? '')
                  return (
                    <label key={key} className="block">
                      <span className="text-xs text-textSecondary">
                        {field.title || key}
                        {field.required && <span className="text-rose-500"> *</span>}
                        {secret && hasSaved && !isCleared && (
                          <span className="ml-1 text-2xs text-mint-500">{t('tool:pluginService.secretSaved')}</span>
                        )}
                        {secret && hasSaved && !isCleared && (
                          <button
                            onClick={(e) => { e.preventDefault(); setCleared(p => ({ ...p, [savedKey(inst.instance, key)]: true })) }}
                            className="ml-2 text-2xs text-rose-500 hover:underline"
                          >
                            {t('tool:pluginService.clear')}
                          </button>
                        )}
                      </span>
                      <input
                        type={secret ? 'password' : 'text'}
                        value={value}
                        onChange={(e) => secret ? setSecret(inst.instance, key, e.target.value) : setValue(inst.instance, key, e.target.value)}
                        placeholder={secret && hasSaved && !isCleared ? t('tool:pluginService.secretKeep') : (field.description || '')}
                        autoComplete="off"
                        disabled={isCleared}
                        className="mt-1 w-full px-2.5 py-1.5 rounded-control border border-border bg-surface text-sm text-textPrimary focus:outline-none focus:border-primary-400 disabled:opacity-50"
                      />
                    </label>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}

      {svc.multi_instance ? (
        <div className="flex items-center gap-2 flex-wrap">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t('tool:pluginService.instanceNameHint')}
            className="px-2.5 py-1.5 rounded-control border border-border bg-surface text-xs text-textPrimary focus:outline-none focus:border-primary-400"
          />
          <button
            onClick={addInstance}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-control text-xs font-medium bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          >
            <Plus size={12} /> {t('tool:pluginService.addInstance')}
          </button>
        </div>
      ) : (
        <p className="text-2xs text-textMuted">{t('tool:pluginService.singleHint')}</p>
      )}
    </div>
  )
}
