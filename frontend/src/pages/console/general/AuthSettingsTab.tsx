import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { Mail, Shield, CheckCircle, XCircle, Loader2, Plus, Trash2, ChevronUp, ChevronDown, X } from 'lucide-react'
import ProviderPresetSelector from '../../../components/ProviderPresetSelector'
import { Dialog } from '../../../components/ui'

interface SmtpConfigItem {
  host: string
  port: number
  username: string
  from_email: string
  from_name: string
  use_tls: boolean
  is_active: boolean
  priority: number
  has_password: boolean
}

interface SmtpFormItem extends SmtpConfigItem {
  password: string
}

interface AuthSettingsData {
  require_email_verification: boolean
  login_providers: string[]
  smtp_configured: boolean
  smtp_config: SmtpConfigItem | null
  smtp_configs: SmtpConfigItem[]
}

interface EmailTemplate {
  subject: string
  body_html: string
}

interface EmailTemplatesData {
  [lang: string]: Record<string, EmailTemplate>
}

const PURPOSES = [
  { key: 'register', labelKey: 'auth.register' },
  { key: 'login', labelKey: 'auth.login' },
  { key: 'rebind', labelKey: 'auth.rebindEmail' },
]

const LANGS: { key: string; label: string }[] = [
  { key: 'zh', label: '中文' },
  { key: 'en', label: 'English' },
]

const PROVIDER_OPTIONS = [
  { key: 'direct', labelKey: 'admin.loginProviderDirect' },
  { key: 'email_code', labelKey: 'admin.loginProviderEmail' },
  { key: 'wechat', labelKey: 'admin.loginProviderWechat' },
  { key: 'qq', labelKey: 'admin.loginProviderQQ' },
]

function emptySmtpForm(): SmtpFormItem {
  return {
    host: '', port: 587, username: '', password: '',
    from_email: '', from_name: 'Copree', use_tls: true,
    is_active: true, priority: 0, has_password: false,
  }
}

function cfgToForm(cfg: SmtpConfigItem): SmtpFormItem {
  return { ...cfg, password: '' }
}

export default function AuthSettingsTab() {
  const t = useT()
  const [settings, setSettings] = useState<AuthSettingsData | null>(null)
  const [loading, setLoading] = useState(true)

  // SMTP 多配置表单
  const [smtpConfigs, setSmtpConfigs] = useState<SmtpFormItem[]>([emptySmtpForm()])
  const [smtpSaving, setSmtpSaving] = useState(false)
  const [smtpTesting, setSmtpTesting] = useState<number | null>(null) // 正在测试的索引
  const [smtpTestResults, setSmtpTestResults] = useState<Record<number, { ok: boolean; msg: string }>>({})

  // 认证设置
  const [reqVerification, setReqVerification] = useState(false)
  const [providers, setProviders] = useState<string[]>(['direct'])
  const [authSaving, setAuthSaving] = useState(false)

  // 邮件模板
  const [templates, setTemplates] = useState<EmailTemplatesData | null>(null)
  const [templatePreset, setTemplatePreset] = useState('gradient')
  const [templateLang, setTemplateLang] = useState('zh')
  const [templatePurpose, setTemplatePurpose] = useState('register')
  const [editSubject, setEditSubject] = useState('')
  const [editBody, setEditBody] = useState('')
  const [tplSaving, setTplSaving] = useState(false)
  const [tplLoading, setTplLoading] = useState(false)

  const loadSettings = useCallback(async () => {
    try {
      const data = await api.get<AuthSettingsData>('/admin/auth-settings')
      setSettings(data)
      setReqVerification(data.require_email_verification)
      setProviders(data.login_providers)
      if (data.smtp_configs && data.smtp_configs.length > 0) {
        setSmtpConfigs(data.smtp_configs.map(cfgToForm))
      } else if (data.smtp_config) {
        setSmtpConfigs([cfgToForm(data.smtp_config)])
      }
    } catch { /* */ } finally { setLoading(false) }
  }, [])

  const loadTemplates = useCallback(async () => {
    setTplLoading(true)
    try {
      const data = await api.get<{ templates: EmailTemplatesData; preset: string }>('/admin/email-templates')
      setTemplates(data.templates)
      setTemplatePreset(data.preset || 'gradient')
    } catch { /* */ } finally { setTplLoading(false) }
  }, [])

  useEffect(() => { loadSettings(); loadTemplates() }, [loadSettings, loadTemplates])

  // 切换模板语言/用途时更新编辑字段
  useEffect(() => {
    if (!templates) return
    const tpl = templates[templateLang]?.[templatePurpose]
    if (tpl) {
      setEditSubject(tpl.subject)
      setEditBody(tpl.body_html)
    }
  }, [templates, templateLang, templatePurpose])

  // ── SMTP 操作 ──

  const updateConfig = (index: number, patch: Partial<SmtpFormItem>) => {
    setSmtpConfigs(prev => prev.map((c, i) => i === index ? { ...c, ...patch } : c))
  }

  const addConfig = () => {
    setSmtpConfigs(prev => [...prev, emptySmtpForm()])
  }

  const removeConfig = (index: number) => {
    if (smtpConfigs.length <= 1) return
    setSmtpConfigs(prev => prev.filter((_, i) => i !== index))
    setSmtpTestResults(prev => {
      const next = { ...prev }
      delete next[index]
      return next
    })
  }

  const moveConfig = (index: number, dir: -1 | 1) => {
    const newIndex = index + dir
    if (newIndex < 0 || newIndex >= smtpConfigs.length) return
    setSmtpConfigs(prev => {
      const next = [...prev]
      const tmp = next[index]
      next[index] = next[newIndex]
      next[newIndex] = tmp
      // 同步更新 priority
      next[index] = { ...next[index], priority: index }
      next[newIndex] = { ...next[newIndex], priority: newIndex }
      return next
    })
  }

  const handleSaveSmtp = async () => {
    setSmtpSaving(true)
    setSmtpTestResults({})
    try {
      await api.put('/admin/smtp-configs', {
        configs: smtpConfigs.map((c, i) => ({
          ...c, priority: i,
        })),
      })
      await loadSettings()
    } catch (err: any) {
      // error handled via test results
    } finally { setSmtpSaving(false) }
  }

  const handleTestSmtp = async (index: number) => {
    setSmtpTesting(index)
    setSmtpTestResults(prev => ({ ...prev, [index]: undefined as any }))
    try {
      const cfg = smtpConfigs[index]
      const res = await api.post<{ success: boolean; message: string }>(
        `/admin/smtp-configs/test/${index}`,
        { host: cfg.host, port: cfg.port, username: cfg.username,
          password: cfg.password || undefined,
          from_email: cfg.from_email, from_name: cfg.from_name, use_tls: cfg.use_tls },
      )
      setSmtpTestResults(prev => ({ ...prev, [index]: { ok: res.success, msg: res.message } }))
    } catch (err: any) {
      setSmtpTestResults(prev => ({ ...prev, [index]: { ok: false, msg: err.message || 'Test failed' } }))
    } finally { setSmtpTesting(null) }
  }

  // ── 认证设置 ──

  const handleSaveAuth = async () => {
    if (providers.length < 1) return
    setAuthSaving(true)
    try {
      await api.put('/admin/auth-settings', {
        require_email_verification: reqVerification,
        login_providers: providers,
      })
      await loadSettings()
    } catch { /* */ } finally { setAuthSaving(false) }
  }

  const toggleProvider = (key: string) => {
    if (providers.includes(key)) {
      if (providers.length <= 1) return
      setProviders(prev => prev.filter(p => p !== key))
    } else {
      setProviders(prev => [...prev, key])
    }
  }

  // ── 邮件模板操作 ──

  const [showPreview, setShowPreview] = useState(false)
  const [previewLang, setPreviewLang] = useState('zh')
  const [previewPurpose, setPreviewPurpose] = useState('register')

  const applyPreviewVars = useCallback((html: string) => {
    return html
      .replace(/\{code\}/g, '483921')
      .replace(/\{from_name\}/g, 'Copree')
      .replace(/\{purpose_label\}/g, '邮箱验证')
      .replace(/\{username\}/g, 'demo_user')
      .replace(/\{instance_name\}/g, 'Copree')
      .replace(/\{expire_minutes\}/g, '5')
  }, [])

  const previewContent = useMemo(() => {
    if (!templates) return null
    const tpl = templates[previewLang]?.[previewPurpose]
    if (!tpl) return null
    return (
      <div className="space-y-3">
        <div className="text-xs text-gray-500 mb-1">
          <span className="font-medium">主题：</span>
          {applyPreviewVars(tpl.subject)}
        </div>
        <iframe
          srcDoc={applyPreviewVars(tpl.body_html)}
          title="email-preview"
          className="w-full border-0 rounded-control"
          style={{ minHeight: 400 }}
          sandbox="allow-same-origin"
        />
      </div>
    )
  }, [templates, previewLang, previewPurpose, applyPreviewVars])

  const handleSaveTemplates = async () => {
    if (!templates) return
    setTplSaving(true)
    try {
      const updated: Record<string, Record<string, EmailTemplate>> = {
        zh: { ...templates.zh },
        en: { ...templates.en },
      }
      updated[templateLang] = {
        ...updated[templateLang],
        [templatePurpose]: { subject: editSubject, body_html: editBody },
      }
      await api.put('/admin/email-templates', { templates: updated })
      setTemplates(updated as EmailTemplatesData)
    } catch { /* */ } finally { setTplSaving(false) }
  }

  const handleResetTemplates = async () => {
    try {
      await api.post('/admin/email-templates/reset')
      await loadTemplates()
    } catch { /* */ }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 size={24} className="animate-spin text-textMuted" />
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* ── SMTP 多配置管理 ── */}
      <section className="bg-surface border border-border rounded-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
            <Mail size={16} className="text-primary-400" />
            {t('admin.smtpConfig')}
          </h3>
          <button
            onClick={addConfig}
            className="btn btn-xs btn-outline gap-1"
          >
            <Plus size={14} />
            {t('admin.smtpAdd')}
          </button>
        </div>

        <div className="space-y-3">
          {smtpConfigs.map((cfg, index) => {
            const testResult = smtpTestResults[index]
            const isTesting = smtpTesting === index
            return (
              <div
                key={index}
                className={`border rounded-control p-4 transition-colors ${
                  cfg.is_active
                    ? 'border-border bg-canvas/50'
                    : 'border-border/50 bg-canvas/30 opacity-60'
                }`}
              >
                {/* 头部：序号 + 主机 + 启用开关 + 操作按钮 */}
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-xs font-mono text-textMuted">#{index + 1}</span>
                  <input
                    type="text"
                    value={cfg.host}
                    onChange={e => updateConfig(index, { host: e.target.value })}
                    className="flex-1 min-w-0 px-2 py-1 text-xs rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                    placeholder="smtp.example.com"
                  />
                  {/* 启用/停用 */}
                  <button
                    onClick={() => updateConfig(index, { is_active: !cfg.is_active })}
                    className={`px-2 py-0.5 text-3xs rounded-full font-medium transition-colors ${
                      cfg.is_active
                        ? 'bg-mint-500/10 text-mint-400 border border-mint-500/20'
                        : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                    }`}
                  >
                    {cfg.is_active ? t('admin.smtpActive') : t('admin.smtpInactive')}
                  </button>
                  {/* 上移/下移 */}
                  <div className="flex gap-0.5">
                    <button
                      onClick={() => moveConfig(index, -1)}
                      disabled={index === 0}
                      className="p-0.5 rounded text-textMuted hover:text-textPrimary disabled:opacity-20 transition-colors"
                    >
                      <ChevronUp size={14} />
                    </button>
                    <button
                      onClick={() => moveConfig(index, 1)}
                      disabled={index === smtpConfigs.length - 1}
                      className="p-0.5 rounded text-textMuted hover:text-textPrimary disabled:opacity-20 transition-colors"
                    >
                      <ChevronDown size={14} />
                    </button>
                  </div>
                  {/* 删除 */}
                  <button
                    onClick={() => removeConfig(index)}
                    disabled={smtpConfigs.length <= 1}
                    className="p-1 rounded text-textMuted hover:text-rose-400 disabled:opacity-20 transition-colors"
                    title={t('admin.smtpDelete')}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>

                {/* 表单字段 */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <div>
                    <label className="block text-3xs text-textMuted mb-0.5">{t('admin.smtpPort')}</label>
                    <input
                      type="number" value={cfg.port}
                      onChange={e => updateConfig(index, { port: parseInt(e.target.value) || 587 })}
                      className="w-full px-2 py-1.5 text-xs rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                    />
                  </div>
                  <div>
                    <label className="block text-3xs text-textMuted mb-0.5">{t('admin.smtpUsername')}</label>
                    <input
                      type="text" value={cfg.username}
                      onChange={e => updateConfig(index, { username: e.target.value })}
                      className="w-full px-2 py-1.5 text-xs rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                    />
                  </div>
                  <div>
                    <label className="block text-3xs text-textMuted mb-0.5">{t('admin.smtpPassword')}</label>
                    <input
                      type="password" value={cfg.password}
                      onChange={e => updateConfig(index, { password: e.target.value })}
                      className="w-full px-2 py-1.5 text-xs rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                      placeholder={cfg.has_password ? t('admin.smtpPasswordPlaceholder') : ''}
                    />
                  </div>
                  <div>
                    <label className="block text-3xs text-textMuted mb-0.5">{t('admin.smtpFromEmail')}</label>
                    <input
                      type="email" value={cfg.from_email}
                      onChange={e => updateConfig(index, { from_email: e.target.value })}
                      className="w-full px-2 py-1.5 text-xs rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                      placeholder="noreply@example.com"
                    />
                  </div>
                  <div>
                    <label className="block text-3xs text-textMuted mb-0.5">{t('admin.smtpFromName')}</label>
                    <input
                      type="text" value={cfg.from_name}
                      onChange={e => updateConfig(index, { from_name: e.target.value })}
                      className="w-full px-2 py-1.5 text-xs rounded border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                    />
                  </div>
                  <div className="flex items-end pb-1">
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox" checked={cfg.use_tls}
                        onChange={e => updateConfig(index, { use_tls: e.target.checked })}
                        className="rounded"
                      />
                      <span className="text-3xs text-textMuted">{t('admin.smtpUseTls')}</span>
                    </label>
                  </div>
                </div>

                {/* 测试结果 + 按钮 */}
                <div className="flex items-center gap-2 mt-3">
                  <button
                    onClick={() => handleTestSmtp(index)}
                    disabled={!cfg.host || !cfg.username || isTesting}
                    className="px-3 py-1.5 text-xs rounded-control border border-border text-textSecondary hover:text-textPrimary hover:bg-elevated disabled:opacity-30 transition-colors"
                  >
                    {isTesting ? t('admin.smtpTesting') : t('admin.smtpTest')}
                  </button>
                  {testResult && (
                    <span className={`text-xs flex items-center gap-1 ${
                      testResult.ok ? 'text-mint-400' : 'text-rose-400'
                    }`}>
                      {testResult.ok ? <CheckCircle size={12} /> : <XCircle size={12} />}
                      {testResult.ok ? t('admin.smtpTestSuccess') : testResult.msg.slice(0, 60)}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        <div className="flex justify-between items-center mt-4">
          <p className="text-3xs text-textMuted">{t('admin.smtpPriority')}</p>
          <button
            onClick={handleSaveSmtp}
            disabled={smtpSaving || smtpConfigs.length < 1}
            className="btn btn-sm btn-primary"
          >
            {smtpSaving ? t('common.saving') : t('common.save')}
          </button>
        </div>
      </section>

      {/* ── 邮件模板编辑（v1.1.0: 预设选择）── */}
      <section className="bg-surface border border-border rounded-card p-5">
        <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2 mb-4">
          <Mail size={16} className="text-accent-400" />
          {t('admin.emailTemplates')}
        </h3>

        {tplLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 size={20} className="animate-spin text-textMuted" />
          </div>
        ) : templates ? (
          <>
            {/* 预设选择 */}
            <div className="flex gap-2 mb-4">
              {(['gradient', 'simple', 'custom'] as const).map(p => (
                <button
                  key={p}
                  onClick={async () => {
                    if (p === templatePreset) return
                    try {
                      await api.put('/admin/email-templates/preset', { preset: p })
                      await loadTemplates()
                    } catch { /* */ }
                  }}
                  className={`flex-1 py-2 text-xs rounded-control font-medium border-2 transition-all ${
                    templatePreset === p
                      ? 'border-primary-400 bg-primary-500/10 text-primary-500 shadow-sm'
                      : 'border-border bg-canvas text-textMuted hover:text-textSecondary hover:border-borderHover'
                  }`}
                >
                  {p === 'gradient' ? t('admin.emailPresetGradient') :
                   p === 'simple' ? t('admin.emailPresetSimple') :
                   t('admin.emailPresetCustom')}
                </button>
              ))}
            </div>

            {/* 自定义版才显示编辑区 */}
            {templatePreset === 'custom' ? (
              <>
                {/* 语言 Tab */}
                <div className="flex gap-1 mb-3">
                  {LANGS.map(l => (
                    <button
                      key={l.key}
                      onClick={() => setTemplateLang(l.key)}
                      className={`px-3 py-1 text-xs rounded-control font-medium transition-colors ${
                        templateLang === l.key
                          ? 'bg-primary-500 text-white'
                          : 'bg-canvas border border-border text-textMuted hover:text-textSecondary'
                      }`}
                    >
                      {l.label}
                    </button>
                  ))}
                </div>

                {/* 用途 Tab */}
                <div className="flex gap-1 mb-4">
                  {PURPOSES.map(p => (
                    <button
                      key={p.key}
                      onClick={() => setTemplatePurpose(p.key)}
                      className={`px-3 py-1 text-xs rounded-control font-medium transition-colors ${
                        templatePurpose === p.key
                          ? 'bg-primary-500/10 border border-primary-500/30 text-primary-500'
                          : 'bg-canvas border border-border text-textMuted hover:text-textSecondary'
                      }`}
                    >
                      {t(p.labelKey)}
                    </button>
                  ))}
                </div>

                {/* 主题 */}
                <div className="mb-3">
                  <label className="block text-xs text-textSecondary mb-1">{t('admin.emailTemplateSubject')}</label>
                  <input
                    type="text"
                    value={editSubject}
                    onChange={e => setEditSubject(e.target.value)}
                    className="w-full px-3 py-2 text-sm rounded-control border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  />
                </div>

                {/* HTML 正文 */}
                <div className="mb-3">
                  <label className="block text-xs text-textSecondary mb-1">{t('admin.emailTemplateBody')}</label>
                  <textarea
                    value={editBody}
                    onChange={e => setEditBody(e.target.value)}
                    rows={10}
                    className="w-full px-3 py-2 text-xs font-mono rounded-control border border-border bg-canvas text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-y"
                  />
                </div>

                {/* 变量提示 */}
                <p className="text-3xs text-textMuted mb-4 bg-canvas rounded-control px-3 py-2 border border-border/50">
                  {t('admin.emailTemplateVarHint')}
                </p>

                {/* 按钮 */}
                <div className="flex gap-2">
                  <button
                    onClick={handleResetTemplates}
                    className="btn btn-xs btn-outline hover:text-rose-400"
                  >
                    {t('admin.emailTemplateResetAll')}
                  </button>
                  <button
                    onClick={handleSaveTemplates}
                    disabled={tplSaving}
                    className="btn btn-xs btn-primary"
                  >
                    {tplSaving ? t('common.saving') : t('admin.emailTemplatesSave')}
                  </button>
                </div>
              </>
            ) : (
              /* 预设版：显示提示信息 + 预览 */
              <div className="bg-mint-400/5 border border-mint-400/20 rounded-card p-5 text-center">
                <div className="w-10 h-10 rounded-full bg-mint-400/20 flex items-center justify-center mx-auto mb-3">
                  <CheckCircle size={20} className="text-mint-400" />
                </div>
                <p className="text-sm font-medium text-textPrimary mb-1">
                  {templatePreset === 'gradient'
                    ? (t('admin.emailPresetGradientActive'))
                    : (t('admin.emailPresetSimpleActive'))}
                </p>
                <p className="text-xs text-textMuted mb-3">
                  {t('admin.emailPresetHint')}
                </p>
                <button
                  onClick={() => setShowPreview(true)}
                  className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 underline underline-offset-2"
                >
                  {t('admin.emailTemplateViewPreview')}
                </button>
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-textMuted py-4 text-center">{t('common.loading')}</p>
        )}
      </section>

      {/* ── 邮件模板预览弹窗 ── */}
      {showPreview && templates && (
        <Dialog onClose={() =>  setShowPreview(false)} className="flex items-center justify-center">
          <div className="relative w-full max-w-3xl max-h-[90vh] bg-surface rounded-dialog border border-border shadow-2xl overflow-hidden flex flex-col" onClick={e => e.stopPropagation()}>
            {/* 头部 */}
            <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
              <h3 className="text-sm font-semibold text-textPrimary">
                {t('admin.emailTemplateViewPreview')}
              </h3>
              <button onClick={() => setShowPreview(false)} className="p-1 rounded-control text-textMuted hover:text-textPrimary hover:bg-elevated transition-colors">
                <X size={16} />
              </button>
            </div>
            {/* 语言 Tab */}
            <div className="flex gap-2 px-5 pt-4 pb-2 shrink-0">
              {LANGS.map(l => (
                <button
                  key={l.key}
                  onClick={() => setPreviewLang(l.key)}
                  className={`px-3 py-1 text-xs rounded-control font-medium transition-colors ${
                    previewLang === l.key
                      ? 'bg-primary-500 text-white'
                      : 'bg-canvas border border-border text-textMuted hover:text-textSecondary'
                  }`}
                >
                  {l.label}
                </button>
              ))}
            </div>
            {/* 用途 Tab */}
            <div className="flex gap-2 px-5 pb-2 shrink-0">
              {PURPOSES.map(p => (
                <button
                  key={p.key}
                  onClick={() => setPreviewPurpose(p.key)}
                  className={`px-3 py-1 text-xs rounded-control font-medium transition-colors ${
                    previewPurpose === p.key
                      ? 'bg-primary-500 text-white'
                      : 'bg-canvas border border-border text-textMuted hover:text-textSecondary'
                  }`}
                >
                  {t(p.labelKey)}
                </button>
              ))}
            </div>
            {/* 预览内容 */}
            <div className="flex-1 overflow-auto p-5 bg-white">
              {previewContent || <p className="text-xs text-textMuted text-center py-8">暂无模板</p>}
            </div>
          </div>
        </Dialog>
      )}

      {/* ── LLM 厂商预设 ── */}
      <ProviderPresetSelector />

      {/* ── 认证设置 ── */}
      <section className="bg-surface border border-border rounded-card p-5">
        <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2 mb-4">
          <Shield size={16} className="text-accent-400" />
          {t('admin.auth')}
        </h3>

        {/* 邮箱验证开关 */}
        <div className="flex items-center justify-between py-3 border-b border-border/60">
          <div>
            <span className="text-sm text-textPrimary">{t('admin.requireEmailVerification')}</span>
            <p className="text-xs text-textMuted mt-0.5">{t('admin.requireEmailVerificationDesc')}</p>
            {!settings?.smtp_configured && (
              <p className="text-xs text-accent-400 mt-1">{t('admin.requireEmailVerificationWarning')}</p>
            )}
          </div>
          <button
            onClick={() => setReqVerification(!reqVerification)}
            disabled={!settings?.smtp_configured}
            className={`relative w-10 h-6 rounded-full transition-colors disabled:opacity-30 ${
              reqVerification ? 'bg-primary-500' : 'bg-border'
            }`}
          >
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
              reqVerification ? 'translate-x-4' : ''
            }`} />
          </button>
        </div>

        {/* 登录方式多选 */}
        <div className="py-3">
          <span className="text-sm text-textPrimary">{t('admin.loginProviders')}</span>
          <p className="text-xs text-textMuted mt-0.5 mb-3">{t('admin.loginProvidersDesc')}</p>
          <div className="flex flex-wrap gap-2">
            {PROVIDER_OPTIONS.map(opt => (
              <button
                key={opt.key}
                onClick={() => toggleProvider(opt.key)}
                disabled={providers.includes(opt.key) && providers.length <= 1}
                className={`px-3 py-1.5 text-xs rounded-control font-medium transition-colors border ${
                  providers.includes(opt.key)
                    ? 'bg-primary-500/10 border-primary-500/30 text-primary-500'
                    : 'bg-canvas border-border text-textMuted hover:text-textSecondary'
                } disabled:opacity-30 disabled:cursor-not-allowed`}
              >
                {t(opt.labelKey)}
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={handleSaveAuth}
          disabled={authSaving || providers.length < 1}
          className="btn btn-sm btn-primary mt-3"
        >
          {authSaving ? t('common.saving') : t('common.save')}
        </button>
      </section>
    </div>
  )
}
