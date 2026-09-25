// OpenCLI：配置 / 授权 AI / 命令 / 日志

import { useState, useEffect } from 'react'
import { ListPanel } from '../../../components/ui'
import { api } from '../../../api/client'
import Toggle from '../../../components/Toggle'
import { useT } from '../../../i18n/I18nContext'

export default function OpenCLITab() {
  const t = useT()
  const [tab, setTab] = useState<'config' | 'agents' | 'commands' | 'logs'>('config')
  const subTabs = [
    { key: 'config' as const, label: t('admin.globalConfig') },
    { key: 'agents' as const, label: t('admin.opencliAiWhitelist') },
    { key: 'commands' as const, label: t('admin.commandWhitelist') },
    { key: 'logs' as const, label: t('admin.usageLogs') },
  ]

  return (
    <div className="space-y-4">
      <div className="flex gap-2 bg-canvas border border-border rounded-card p-1 w-full overflow-x-auto">
        {subTabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-1.5 text-sm rounded-control transition-colors font-medium ${
              tab === t.key
                ? 'bg-elevated text-textPrimary shadow-sm'
                : 'text-textMuted hover:text-textSecondary hover:bg-elevated'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'config' && <OpenCLIConfigSection />}
      {tab === 'agents' && <OpenCLIAgentsSection />}
      {tab === 'commands' && <OpenCLICommandsSection />}
      {tab === 'logs' && <OpenCLILogsSection />}
    </div>
  )
}

// ---- 全局设置 ----

function OpenCLIConfigSection() {
  const t = useT()
  const [config, setConfig] = useState<any>(null)
  const [enabled, setEnabled] = useState(false)
  const [rate, setRate] = useState(5)
  const [timeout, setTimeout_] = useState(30)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/admin/opencli/config').then((d) => {
      setConfig(d)
      setEnabled(d.global_enabled)
      setRate(d.default_rate_limit_per_minute)
      setTimeout_(d.timeout_seconds)
    }).catch(console.error)
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.put('/admin/opencli/config', {
        global_enabled: enabled,
        default_rate_limit_per_minute: rate,
        timeout_seconds: timeout,
      })
      alert(t('admin.saveSuccess'))
    } catch (err) { console.error(err) }
    setSaving(false)
  }

  if (!config) return <p className="text-textMuted">{t('common.loading')}</p>

  return (
    <div className="bg-surface rounded-card border border-border p-5 max-w-lg">
      <h3 className="font-semibold text-textPrimary mb-4">{t('admin.globalConfig')}</h3>
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Toggle checked={enabled} onChange={setEnabled} label={t('admin.enableOpenCLI')} />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1 text-textSecondary">{t('admin.rateLimit')}</label>
          <input type="number" value={rate} onChange={(e) => setRate(parseInt(e.target.value))}
            className="w-24 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary" />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1 text-textSecondary">{t('admin.timeoutSeconds')}</label>
          <input type="number" value={timeout} onChange={(e) => setTimeout_(parseInt(e.target.value))}
            className="w-24 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary" />
        </div>
        <button onClick={handleSave} disabled={saving}
          className="btn btn-sm btn-primary">
          {saving ? t('admin.saving') : t('admin.save')}
        </button>
      </div>
    </div>
  )
}

// ---- AI 白名单 ----

function OpenCLIAgentsSection() {
  const t = useT()
  const [data, setData] = useState<any[]>([])
  useEffect(() => {
    api.get('/admin/opencli/agents').then(setData).catch(console.error)
  }, [])

  const toggleAgent = async (agentId: number, currentEnabled: boolean) => {
    await api.put(`/admin/opencli/agents/${agentId}`, { enabled: !currentEnabled })
    const newData = await api.get('/admin/opencli/agents')
    setData(newData)
  }

  if (!data.length) return <p className="text-textMuted">{t('common.loading')}</p>

  return (
    <div className="bg-surface rounded-card border border-border p-5">
      <h3 className="font-semibold text-textPrimary mb-3">{t('admin.opencliAiWhitelist')}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-textPrimary">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.opencliAiWhitelistColId')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.opencliAiWhitelistColName')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.opencliAiWhitelistColOwner')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.opencliAiWhitelistColEnabled')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.opencliAiWhitelistColRate')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.opencliAiWhitelistColAction')}</th>
            </tr>
          </thead>
          <tbody>
            {data.map((a: any) => (
              <tr key={a.agent_id} className="border-b border-border/50">
                <td className="py-2 px-3">{a.agent_id}</td>
                <td className="py-2 px-3 font-medium">{a.agent_name}</td>
                <td className="py-2 px-3">{a.owner_id}</td>
                <td className="py-2 px-3">
                  <span className={a.enabled ? 'text-mint-400' : 'text-textMuted'}>
                    {a.enabled ? t('common.enabled') : t('common.disabled')}
                  </span>
                </td>
                <td className="py-2 px-3">{a.actual_rate_limit}/min</td>
                <td className="py-2 px-3">
                  <button
                    onClick={() => toggleAgent(a.agent_id, a.enabled)}
                    className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300"
                  >
                    {a.enabled ? t('common.disabled') : t('common.enabled')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---- 命令白名单 ----
// 预设命令定义：与后端 POST /admin/opencli/commands/presets 保持一致
//    每个预设包含 pattern（命令名或正则）、is_regex、description（i18n key）、category（i18n key）

function OpenCLICommandsSection() {
  const t = useT()
  const OPENCLI_PRESETS = [
    // ── 文件操作（AI 在自己的沙箱目录里读写，进程内 Python 实现） ──
    { pattern: 'file_read',   is_regex: false, description: t('opencli.preset.fileRead'), category: t('opencli.category.fileOps') },
    { pattern: 'file_write',  is_regex: false, description: t('opencli.preset.fileWrite'), category: t('opencli.category.fileOps') },
    { pattern: 'file_list',   is_regex: false, description: t('opencli.preset.fileList'), category: t('opencli.category.fileOps') },
    { pattern: 'file_delete', is_regex: false, description: t('opencli.preset.fileDelete'), category: t('opencli.category.fileOps') },
    { pattern: 'file_info',   is_regex: false, description: t('opencli.preset.fileInfo'), category: t('opencli.category.fileOps') },
    { pattern: 'create_dir',  is_regex: false, description: t('opencli.preset.createDir'), category: t('opencli.category.fileOps') },
    // ── 浏览器自动化（操控已登录的 Chrome 浏览器） ──
    { pattern: 'browser',   is_regex: false, description: t('opencli.preset.browser'), category: t('opencli.category.browser') },
    { pattern: 'list',      is_regex: false, description: t('opencli.preset.listCmds'), category: t('opencli.category.browser') },
    // ── 外部 CLI 桥接（将已有命令行工具接入 OpenCLI） ──
    { pattern: 'gh .*',     is_regex: true,  description: t('opencli.preset.ghCli'), category: t('opencli.category.cliBridge') },
    { pattern: 'docker .*', is_regex: true,  description: t('opencli.preset.dockerCli'), category: t('opencli.category.cliBridge') },
    { pattern: 'obsidian .*', is_regex: true, description: t('opencli.preset.obsidianCli'), category: t('opencli.category.cliBridge') },
    { pattern: 'vercel .*', is_regex: true,  description: t('opencli.preset.vercelCli'), category: t('opencli.category.cliBridge') },
    { pattern: 'tg .*',     is_regex: true,  description: t('opencli.preset.tgCli'), category: t('opencli.category.cliBridge') },
    { pattern: 'discord .*', is_regex: true, description: t('opencli.preset.discordCli'), category: t('opencli.category.cliBridge') },
    { pattern: 'wx .*',     is_regex: true,  description: t('opencli.preset.wxCli'), category: t('opencli.category.cliBridge') },
  ]
  const [data, setData] = useState<any[]>([])
  const [pattern, setPattern] = useState('')
  const [isRegex, setIsRegex] = useState(false)
  const [desc, setDesc] = useState('')
  const [addingPresets, setAddingPresets] = useState(false)

  const load = async () => {
    const d = await api.get('/admin/opencli/commands')
    setData(Array.isArray(d) ? d : d.items || [])
  }

  useEffect(() => { load() }, [])

  // 检查某个预设是否已在白名单中（按 pattern + is_regex 匹配）
  const isPresetAdded = (pattern: string, isRegex: boolean) => {
    return data.some((c: any) => c.pattern === pattern && c.is_regex === isRegex)
  }

  // 获取已添加预设的当前状态
  const getPresetStatus = (pattern: string, isRegex: boolean) => {
    const found = data.find((c: any) => c.pattern === pattern && c.is_regex === isRegex)
    return found ? (found.enabled ? t('common.enabled') : t('common.disabled')) : t('opencli.status.notAdded')
  }

  const handleAdd = async () => {
    if (!pattern.trim()) return
    await api.post('/admin/opencli/commands', { pattern: pattern.trim(), is_regex: isRegex, description: desc || null })
    setPattern(''); setIsRegex(false); setDesc('')
    load()
  }

  // 一键添加所有预设（调用后端批量 API）
  const handleAddAllPresets = async () => {
    setAddingPresets(true)
    try {
      const result = await api.post('/admin/opencli/commands/presets')
      alert(result.message || t('admin.presetsAddComplete'))
      load()
    } catch (err: any) {
      alert(err.message || t('admin.presetsAddFailed'))
    }
    setAddingPresets(false)
  }

  // 添加单个预设
  const handleAddPreset = async (presetPattern: string, presetIsRegex: boolean, presetDesc: string) => {
    await api.post('/admin/opencli/commands', {
      pattern: presetPattern,
      is_regex: presetIsRegex,
      description: presetDesc,
    })
    load()
  }

  const handleToggle = async (id: number, enabled: boolean) => {
    await api.put(`/admin/opencli/commands/${id}/toggle?enabled=${!enabled}`)
    load()
  }

  const handleDelete = async (id: number) => {
    if (!confirm(t('admin.confirmDeletePoolKey').replace('{name}', ''))) return
    await api.delete(`/admin/opencli/commands/${id}`)
    load()
  }

  // 按类别分组预设
  const presetCategories = [...new Set(OPENCLI_PRESETS.map(p => p.category))]

  return (
    <div className="space-y-4">
      {/* ── 预设命令快速添加（新手友好） ── */}
      <div className="bg-surface rounded-card border border-border p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="font-semibold text-textPrimary">{t('admin.presetCommands')}</h3>
            <p className="text-xs text-textMuted mt-1">
              {t('admin.presetCommandsDesc')}
            </p>
          </div>
          <button
            onClick={handleAddAllPresets}
            disabled={addingPresets}
            className="px-4 py-2 bg-mint-500 text-white rounded-card hover:bg-mint-400 disabled:opacity-50 text-sm font-medium transition-colors"
          >
            {addingPresets ? t('common.saving') : t('admin.addAllPresets')}
          </button>
        </div>

        {presetCategories.map(cat => {
          const catPresets = OPENCLI_PRESETS.filter(p => p.category === cat)
          const allAdded = catPresets.every(p => isPresetAdded(p.pattern, p.is_regex))
          return (
            <div key={cat} className="mb-3 last:mb-0">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs font-semibold text-textSecondary uppercase tracking-wider">{cat}</span>
                <span className={`text-xs px-1.5 py-0.5 rounded-full ${allAdded ? 'bg-mint-400/10 text-mint-400' : 'bg-accent-400/10 text-accent-400'}`}>
                  {allAdded ? t('admin.allAdded') : `${catPresets.filter(p => isPresetAdded(p.pattern, p.is_regex)).length}/${catPresets.length}`}
                </span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
                {catPresets.map(p => {
                  const added = isPresetAdded(p.pattern, p.is_regex)
                  const status = getPresetStatus(p.pattern, p.is_regex)
                  return (
                    <div
                      key={`${p.pattern}-${p.is_regex}`}
                      className={`rounded-control border p-3 text-sm transition-colors ${
                        added
                          ? 'border-mint-400/30 bg-mint-400/5'
                          : 'border-border bg-canvas hover:border-primary-400/30'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <code className="text-xs font-mono text-textPrimary break-all">{p.pattern}</code>
                          <span className="text-xs text-textMuted ml-1.5">{p.is_regex ? `(${t('admin.regex')})` : `(${t('admin.exact')})`}</span>
                          <p className="text-xs text-textSecondary mt-1 leading-relaxed">{p.description}</p>
                        </div>
                        <button
                          onClick={() => !added && handleAddPreset(p.pattern, p.is_regex, p.description)}
                          disabled={added}
                          className={`shrink-0 px-2.5 py-1 rounded-control text-xs font-medium transition-colors ${
                            added
                              ? 'bg-mint-400/10 text-mint-400 cursor-default'
                              : 'bg-primary-500 text-white hover:bg-primary-600'
                          }`}
                        >
                          {added ? status : '+ ' + t('admin.addCmd')}
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      {/* ── 手动添加表单 ── */}
      <div className="bg-surface rounded-card border border-border p-5 max-w-lg">
        <h3 className="font-semibold mb-3 text-textPrimary">{t('admin.manualAddCommand')}</h3>
        <p className="text-xs text-textMuted mb-3" dangerouslySetInnerHTML={{ __html: t('admin.manualAddDesc') }} />
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin.commandPattern')}</label>
            <input value={pattern} onChange={(e) => setPattern(e.target.value)}
              className="w-40 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary"
              placeholder={t('admin.commandPatternPlaceholder')} />
          </div>
          <div className="flex items-center gap-1.5 mb-1">
            <input type="checkbox" checked={isRegex} onChange={(e) => setIsRegex(e.target.checked)}
              className="rounded" />
            <span className="text-xs text-textSecondary">{t('admin.regexMode')}</span>
          </div>
          <div>
            <label className="block text-xs mb-1 text-textSecondary">{t('admin.description')}</label>
            <input value={desc} onChange={(e) => setDesc(e.target.value)}
              className="w-32 px-2 py-1.5 border border-border bg-canvas rounded-card text-sm text-textPrimary"
              placeholder={t('common.optional')} />
          </div>
          <button onClick={handleAdd}
            className="btn btn-xs btn-primary">
            {t('admin.addCmd')}
          </button>
        </div>
      </div>

      {/* ── 白名单列表 ── */}
      <ListPanel
        title={t('admin.commandWhitelist')}
        columns={[
          { key: 'pattern', label: t('admin.cmdColPattern') },
          { key: 'type', label: t('admin.cmdColType') },
          { key: 'desc', label: t('admin.cmdColDesc') },
          { key: 'default', label: '默认' },
          { key: 'status', label: t('admin.cmdColStatus'), className: 'w-px whitespace-nowrap' },
          { key: 'action', label: t('admin.cmdColAction'), className: 'w-px whitespace-nowrap' },
        ]}
        empty={data.length === 0 ? <p className="text-center text-textMuted">{t('admin.noCommands')}</p> : undefined}
      >
              {data.map((c: any) => (
                <tr key={c.id} className="border-b border-border/50">
                  <td className="py-2 px-3 font-mono text-xs text-textPrimary">{c.pattern}</td>
                  <td className="py-2 px-3 text-xs text-textSecondary">{c.is_regex ? t('admin.regex') : t('admin.exact')}</td>
                  <td className="py-2 px-3 text-xs text-textSecondary">{c.description || '-'}</td>
                  <td className="py-2 px-3">
                    <button onClick={async () => {
                      await api.put(`/admin/opencli/commands/${c.id}/default?default_enabled=${!c.default_enabled}`)
                      load()
                    }} className={`text-xs px-2 py-0.5 rounded-full font-medium ${c.default_enabled ? 'bg-mint-400/10 text-mint-400' : 'bg-gray-100 text-gray-400'}`}>
                      {c.default_enabled ? '全部AI' : '白名单'}
                    </button>
                  </td>
                  <td className="py-2 px-3">
                    <span className={c.enabled ? 'text-mint-400 text-xs' : 'text-textMuted text-xs'}>
                      {c.enabled ? t('common.enabled') : t('common.disabled')}
                    </span>
                  </td>
                  <td className="py-2 px-3 flex gap-2">
                    <button onClick={() => handleToggle(c.id, c.enabled)}
                      className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300">
                      {c.enabled ? t('common.disabled') : t('common.enabled')}
                    </button>
                    <button onClick={() => handleDelete(c.id)}
                      className="text-xs text-rose-400 hover:text-rose-500 dark:hover:text-rose-300">
                      {t('common.delete')}
                    </button>
                  </td>
                </tr>
              ))}
      </ListPanel>
    </div>
  )
}

// ---- 使用日志 ----

function OpenCLILogsSection() {
  const t = useT()
  const [data, setData] = useState<any>(null)
  const [page, setPage] = useState(1)

  useEffect(() => {
    api.get(`/admin/opencli/logs?page=${page}&page_size=30`).then(setData).catch(console.error)
  }, [page])

  if (!data) return <p className="text-textMuted">{t('common.loading')}</p>

  return (
    <div className="bg-surface rounded-card border border-border p-5">
      <h3 className="font-semibold text-textPrimary mb-3">{t('admin.usageLogs')}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-textPrimary">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColTime')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColAi')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColCmd')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColExitCode')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColDuration')}</th>
              <th className="text-left py-2 px-3 font-medium text-textSecondary">{t('admin.logsColOutput')}</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((log: any) => (
              <tr key={log.id} className="border-b border-border/50">
                <td className="py-2 px-3 text-xs text-textSecondary">
                  {log.executed_at ? new Date(log.executed_at).toLocaleString('zh-CN') : '-'}
                </td>
                <td className="py-2 px-3 text-xs text-textPrimary">AI #{log.agent_id}</td>
                <td className="py-2 px-3 font-mono text-xs text-textPrimary">
                  {log.command}{log.args ? ` ${log.args}` : ''}
                </td>
                <td className="py-2 px-3">
                  <span className={log.exit_code === 0 ? 'text-mint-400 text-xs' : 'text-rose-400 text-xs'}>
                    {log.exit_code}
                  </span>
                </td>
                <td className="py-2 px-3 text-xs text-textSecondary">{log.duration_ms}ms</td>
                <td className="py-2 px-3 text-xs text-textSecondary max-w-[250px] truncate">
                  {log.stdout_truncated || log.stderr_truncated || '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2 mt-3">
        <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}
          className="text-sm px-3 py-1 border border-border bg-canvas rounded hover:bg-elevated disabled:opacity-40 text-textSecondary">
          {t('common.prevPage')}
        </button>
        <button onClick={() => setPage(p => p + 1)} disabled={data.items.length < data.page_size}
          className="text-sm px-3 py-1 border border-border bg-canvas rounded hover:bg-elevated disabled:opacity-40 text-textSecondary">
          {t('common.nextPage')}
        </button>
      </div>
    </div>
  )
}


// ════════════════════════════════════════════════════════════
// 平台设置 Tab（全局默认语言等）
// ════════════════════════════════════════════════════════════
