import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { Mail, Shield, CheckCircle, XCircle, Loader2, Plus, Trash2, ChevronUp, ChevronDown, X } from 'lucide-react'
import ProviderPresetSelector from '../../../components/ProviderPresetSelector'
import { Badge, Button, Dialog, IconButton, INSET_CARD, SecretInput } from '../../../components/ui'

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
  { key: 'register', labelKey: 'auth:register' },
  { key: 'login', labelKey: 'auth:login' },
  { key: 'rebind', labelKey: 'auth:rebindEmail' },
]

const LANGS: { key: string; label: string }[] = [
  { key: 'zh', label: '中文' },
  { key: 'en', label: 'English' },
]

const PROVIDER_OPTIONS = [
  { key: 'direct', labelKey: 'admin:loginProviderDirect' },
  { key: 'email_code', labelKey: 'admin:loginProviderEmail' },
  { key: 'wechat', labelKey: 'admin:loginProviderWechat' },
  { key: 'qq', labelKey: 'admin:loginProviderQQ' },
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
    <div className="columns-1 xl:columns-2 gap-4">
      {/* ── SMTP 多配置管理 ── */}
      <section className="break-inside-avoid mb-4 card p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
            <Mail size={16} className="text-primary-400" />
            {t('admin:smtpConfig')}
          </h3>
          <Button variant="outline" icon={<Plus size={14} />} onClick={addConfig}>
            {t('admin:smtpAdd')}
          </Button>
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
                    className="field field-sm flex-1 min-w-0"
                    placeholder="smtp.example.com"
                  />
                  {/* 启用/停用 */}
                  <button
                    onClick={() => updateConfig(index, { is_active: !cfg.is_active })}
                    className="shrink-0"
                  >
                    <Badge tone={cfg.is_active ? 'mint' : 'rose'}>
                      {cfg.is_active ? t('admin:smtpActive') : t('admin:smtpInactive')}
                    </Badge>
                  </button>
                  {/* 上移/下移 */}
                  <div className="flex gap-0.5 shrink-0">
                    <IconButton
                      icon={<ChevronUp size={14} />}
                      label={t('admin:smtpMoveUp')}
                      size="sm"
                      onClick={() => moveConfig(index, -1)}
                      disabled={index === 0}
                    />
                    <IconButton
                      icon={<ChevronDown size={14} />}
                      label={t('admin:smtpMoveDown')}
                      size="sm"
                      onClick={() => moveConfig(index, 1)}
                      disabled={index === smtpConfigs.length - 1}
                    />
                  </div>
                  {/* 删除 */}
                  <IconButton
                    icon={<Trash2 size={14} />}
                    label={t('admin:smtpDelete')}
                    size="sm"
                    tone="danger"
                    onClick={() => removeConfig(index)}
                    disabled={smtpConfigs.length <= 1}
                  />
                </div>

                {/* 表单字段 */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <div>
                    <label className="block text-xs text-textSecondary mb-1">{t('admin:smtpPort')}</label>
                    <input
                      type="number" value={cfg.port}
                      onChange={e => updateConfig(index, { port: parseInt(e.target.value) || 587 })}
                      className="field field-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-textSecondary mb-1">{t('admin:smtpUsername')}</label>
                    <input
                      type="text" value={cfg.username}
                      onChange={e => updateConfig(index, { username: e.target.value })}
                      className="field field-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-textSecondary mb-1">{t('admin:smtpPassword')}</label>
                    <SecretInput value={cfg.password}
                      fieldSize="sm"
                      onChange={e => updateConfig(index, { password: e.target.value })}
                      wrapperClassName="w-full"
                      placeholder={cfg.has_password ? t('admin:smtpPasswordPlaceholder') : ''}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-textSecondary mb-1">{t('admin:smtpFromEmail')}</label>
                    <input
                      type="email" value={cfg.from_email}
                      onChange={e => updateConfig(index, { from_email: e.target.value })}
                      className="field field-sm"
                      placeholder="noreply@example.com"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-textSecondary mb-1">{t('admin:smtpFromName')}</label>
                    <input
                      type="text" value={cfg.from_name}
                      onChange={e => updateConfig(index, { from_name: e.target.value })}
                      className="field field-sm"
                    />
                  </div>
                  <div className="flex items-end pb-1">
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox" checked={cfg.use_tls}
                        onChange={e => updateConfig(index, { use_tls: e.target.checked })}
                        className="rounded"
                      />
                      <span className="text-3xs text-textMuted">{t('admin:smtpUseTls')}</span>
                    </label>
                  </div>
                </div>

                {/* 测试结果 + 按钮 */}
                <div className="flex items-center gap-2 mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleTestSmtp(index)}
                    disabled={!cfg.host || !cfg.username}
                    loading={isTesting}
                  >
                    {isTesting ? t('admin:smtpTesting') : t('admin:smtpTest')}
                  </Button>
                  {testResult && (
                    <span className={`text-xs flex items-center gap-1 ${
                      testResult.ok ? 'text-mint-400' : 'text-rose-400'
                    }`}>
                      {testResult.ok ? <CheckCircle size={12} /> : <XCircle size={12} />}
                      {testResult.ok ? t('admin:smtpTestSuccess') : testResult.msg.slice(0, 60)}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        <div className="flex justify-between items-center mt-4">
          <p className="text-3xs text-textMuted">{t('admin:smtpPriority')}</p>
          <Button
            size="sm"
            onClick={handleSaveSmtp}
            disabled={smtpConfigs.length < 1}
            loading={smtpSaving}
          >
            {smtpSaving ? t('common:saving') : t('common:save')}
          </Button>
        </div>
      </section>

      {/* ── 邮件模板编辑（v1.1.0: 预设选择）── */}
      <section className="break-inside-avoid mb-4 card p-5">
        <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2 mb-4">
          <Mail size={16} className="text-accent-400" />
          {t('admin:emailTemplates')}
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
                <Button
                  key={p}
                  variant="outline"
                  size="sm"
                  className={`flex-1 ${templatePreset === p ? 'is-active' : ''}`}
                  onClick={async () => {
                    if (p === templatePreset) return
                    try {
                      await api.put('/admin/email-templates/preset', { preset: p })
                      await loadTemplates()
                    } catch { /* */ }
                  }}
                >
                  {p === 'gradient' ? t('admin:emailPresetGradient') :
                   p === 'simple' ? t('admin:emailPresetSimple') :
                   t('admin:emailPresetCustom')}
                </Button>
              ))}
            </div>

            {/* 自定义版才显示编辑区 */}
            {templatePreset === 'custom' ? (
              <>
                {/* 语言 Tab */}
                <div className="flex gap-1 mb-3">
                  {LANGS.map(l => (
                    <Button
                      key={l.key}
                      variant="ghost"
                      size="xs"
                      className={templateLang === l.key ? 'is-active' : ''}
                      onClick={() => setTemplateLang(l.key)}
                    >
                      {l.label}
                    </Button>
                  ))}
                </div>

                {/* 用途 Tab */}
                <div className="flex gap-1 mb-4">
                  {PURPOSES.map(p => (
                    <Button
                      key={p.key}
                      variant="ghost"
                      size="xs"
                      className={templatePurpose === p.key ? 'is-active' : ''}
                      onClick={() => setTemplatePurpose(p.key)}
                    >
                      {t(p.labelKey)}
                    </Button>
                  ))}
                </div>

                {/* 主题 */}
                <div className="mb-3">
                  <label className="block text-xs text-textSecondary mb-1">{t('admin:emailTemplateSubject')}</label>
                  <input
                    type="text"
                    value={editSubject}
                    onChange={e => setEditSubject(e.target.value)}
                    className="field"
                  />
                </div>

                {/* HTML 正文 */}
                <div className="mb-3">
                  <label className="block text-xs text-textSecondary mb-1">{t('admin:emailTemplateBody')}</label>
                  <textarea
                    value={editBody}
                    onChange={e => setEditBody(e.target.value)}
                    rows={10}
                    className="field font-mono"
                  />
                </div>

                {/* 变量提示 */}
                <p className={`text-3xs text-textMuted mb-4 px-3 py-2 ${INSET_CARD}`}>
                  {t('admin:emailTemplateVarHint')}
                </p>

                {/* 按钮 */}
                <div className="flex justify-end gap-2">
                  <Button variant="outline" size="sm" onClick={handleResetTemplates}>
                    {t('admin:emailTemplateResetAll')}
                  </Button>
                  <Button size="sm" onClick={handleSaveTemplates} loading={tplSaving}>
                    {tplSaving ? t('common:saving') : t('admin:emailTemplatesSave')}
                  </Button>
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
                    ? (t('admin:emailPresetGradientActive'))
                    : (t('admin:emailPresetSimpleActive'))}
                </p>
                <p className="text-xs text-textMuted mb-3">
                  {t('admin:emailPresetHint')}
                </p>
                <Button variant="link" size="sm" onClick={() => setShowPreview(true)}>
                  {t('admin:emailTemplateViewPreview')}
                </Button>
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-textMuted py-4 text-center">{t('common:loading')}</p>
        )}
      </section>

      {/* ── 邮件模板预览弹窗 ── */}
      {showPreview && templates && (
        <Dialog onClose={() =>  setShowPreview(false)} className="flex items-center justify-center">
          <div className="relative w-full max-w-3xl max-h-[90vh] bg-surface rounded-dialog border border-border shadow-2xl overflow-hidden flex flex-col" onClick={e => e.stopPropagation()}>
            {/* 头部 */}
            <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
              <h3 className="text-sm font-semibold text-textPrimary">
                {t('admin:emailTemplateViewPreview')}
              </h3>
              <IconButton icon={<X size={16} />} label={t('common:close')} onClick={() => setShowPreview(false)} />
            </div>
            {/* 语言 Tab */}
            <div className="flex gap-2 px-5 pt-4 pb-2 shrink-0">
              {LANGS.map(l => (
                <Button
                  key={l.key}
                  variant="ghost"
                  size="xs"
                  className={previewLang === l.key ? 'is-active' : ''}
                  onClick={() => setPreviewLang(l.key)}
                >
                  {l.label}
                </Button>
              ))}
            </div>
            {/* 用途 Tab */}
            <div className="flex gap-2 px-5 pb-2 shrink-0">
              {PURPOSES.map(p => (
                <Button
                  key={p.key}
                  variant="ghost"
                  size="xs"
                  className={previewPurpose === p.key ? 'is-active' : ''}
                  onClick={() => setPreviewPurpose(p.key)}
                >
                  {t(p.labelKey)}
                </Button>
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
      <div className="break-inside-avoid mb-4">
        <ProviderPresetSelector />
      </div>

      {/* ── 认证设置 ── */}
      <section className="break-inside-avoid mb-4 card p-5">
        <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2 mb-4">
          <Shield size={16} className="text-accent-400" />
          {t('admin:auth')}
        </h3>

        {/* 邮箱验证开关 */}
        <div className="flex items-center justify-between py-3 border-b border-border/60">
          <div>
            <span className="text-sm text-textPrimary">{t('admin:requireEmailVerification')}</span>
            <p className="text-xs text-textMuted mt-0.5">{t('admin:requireEmailVerificationDesc')}</p>
            {!settings?.smtp_configured && (
              <p className="text-xs text-accent-400 mt-1">{t('admin:requireEmailVerificationWarning')}</p>
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
          <span className="text-sm text-textPrimary">{t('admin:loginProviders')}</span>
          <p className="text-xs text-textMuted mt-0.5 mb-3">{t('admin:loginProvidersDesc')}</p>
          <div className="flex flex-wrap gap-2">
            {PROVIDER_OPTIONS.map(opt => (
              <button
                key={opt.key}
                onClick={() => toggleProvider(opt.key)}
                disabled={providers.includes(opt.key) && providers.length <= 1}
                className="btn btn-sm btn-outline"
              >
                {t(opt.labelKey)}
              </button>
            ))}
          </div>
        </div>

        <div className="flex justify-end mt-3">
          <Button
            size="sm"
            onClick={handleSaveAuth}
            disabled={authSaving || providers.length < 1}
            loading={authSaving}
          >
            {authSaving ? t('common:saving') : t('common:save')}
          </Button>
        </div>
      </section>
    </div>
  )
}
