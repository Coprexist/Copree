// 平台设置：站点 / 注册 / 语言等分组配置

import { useState, useEffect, useRef } from 'react'
import { api } from '../../../api/client'
import Toggle from '../../../components/Toggle'
import { useT } from '../../../i18n/I18nContext'
import { LANGUAGES } from '../../../i18n/languages'
import ConfigGroupCard from '../../../components/ConfigGroupCard'

export default function SystemSettingsTab() {
  const t = useT()
  const [config, setConfig] = useState<any>(null)
  const [lang, setLang] = useState('en')
  const [platformCredit, setPlatformCredit] = useState(0)
  const [fileQuota, setFileQuota] = useState(100)
  const [uploadMaxSizeMb, setUploadMaxSizeMb] = useState(32)
  const [avatarMaxSizeMb, setAvatarMaxSizeMb] = useState(10)
  const [defaultConcurrentAiLimit, setDefaultConcurrentAiLimit] = useState(3)
  const [registrationEnabled, setRegistrationEnabled] = useState(true)
  const regToggleRef = useRef(false)
  const [auditUserActions, setAuditUserActions] = useState(false)
  const [auditRetention, setAuditRetention] = useState(90)
  const [messageRetention, setMessageRetention] = useState(0)
  const [dailyBackupEnabled, setDailyBackupEnabled] = useState(false)
  const [dailyBackupKeep, setDailyBackupKeep] = useState(7)
  const [geoipUrl, setGeoipUrl] = useState('')
  const [bulkConcurrency, setBulkConcurrency] = useState(3)
  const [bulking, setBulking] = useState(false)
  const [hasActiveKeys, setHasActiveKeys] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    Promise.all([
      api.get('/admin/system-settings'),
      api.get('/admin/api-key-pool'),
      api.get('/admin/upload-limits'),
    ]).then(([settings, keys, limits]) => {
      setConfig(settings)
      setLang(settings.default_language || 'en')
      setPlatformCredit(settings.default_platform_credit || 0)
      setFileQuota(settings.default_file_quota_mb ?? 100)
      setUploadMaxSizeMb(limits.upload_max_size_mb ?? 32)
      setAvatarMaxSizeMb(limits.avatar_max_size_mb ?? 10)
      setDefaultConcurrentAiLimit(settings.default_concurrent_ai_limit ?? 3)
      setRegistrationEnabled(settings.registration_enabled ?? true)
      setAuditUserActions(settings.audit_user_actions ?? false)
      setAuditRetention(settings.audit_log_retention_days ?? 90)
      setMessageRetention(settings.message_retention_days ?? 0)
      setDailyBackupEnabled(settings.daily_backup_enabled ?? false)
      setDailyBackupKeep(settings.daily_backup_keep ?? 7)
      setGeoipUrl(settings.geoip_provider_url || '')
      setHasActiveKeys(keys.some((k: any) => k.is_active))
    }).catch(console.error)
  }, [])

  const handleSave = async (field: string, value: any) => {
    setSaving(true)
    setMsg('')
    try {
      const payload: any = {}
      if (field === 'language') payload.default_language = value
      else if (field === 'platform_credit') payload.default_platform_credit = value
      else if (field === 'file_quota') payload.default_file_quota_mb = value
      else if (field === 'concurrent_ai_limit') payload.default_concurrent_ai_limit = value
      else if (field === 'registration_enabled') payload.registration_enabled = value
      else if (field === 'audit_user_actions') payload.audit_user_actions = value
      else if (field === 'audit_retention') payload.audit_log_retention_days = value
      else if (field === 'message_retention') payload.message_retention_days = value
      else if (field === 'daily_backup_enabled') payload.daily_backup_enabled = value
      else if (field === 'daily_backup_keep') payload.daily_backup_keep = value
      else if (field === 'geoip_url') payload.geoip_provider_url = value || null
      const updated = await api.put('/admin/system-settings', payload)
      setConfig(updated)
      setMsg(t('admin.saveSuccess'))
    } catch (err: any) {
      setMsg(err?.message || err?.detail || t('admin.saveFailed'))
    }
    setSaving(false)
  }

  const handlePlatformCreditSave = () => {
    const old = config?.default_platform_credit || 0
    if (platformCredit === old) return
    if (platformCredit > 0 && !hasActiveKeys) {
      setMsg(t('admin.platformCreditNoActiveKey'))
      return
    }
    const delta = platformCredit - old
    const confirmed = confirm(
      t('admin.platformCreditConfirm')
        .replace('{old}', String(old))
        .replace('{new}', String(platformCredit))
        .replace('{delta}', (delta >= 0 ? '+' : '') + delta)
        .replace('{userCount}', t('admin.allUsers'))
    )
    if (!confirmed) return
    handleSave('platform_credit', platformCredit)
  }

  if (!config) return <p className="text-textMuted p-6">{t('common.loading')}</p>

  return (
    <div className="bg-surface rounded-card border border-border p-5 max-w-lg space-y-6">
      <h3 className="font-semibold text-textPrimary">{t('admin.systemSettings')}</h3>

      {/* 默认语言 */}
      <div>
        <label className="block text-sm font-medium mb-1 text-textSecondary">{t('admin.defaultLanguage')}</label>
        <p className="text-xs text-textMuted mb-2">{t('admin.defaultLanguageDesc')}</p>
        <select
          value={lang}
          onChange={(e) => {
            setLang(e.target.value)
            handleSave('language', e.target.value)
          }}
          className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-sm text-textPrimary"
        >
          {LANGUAGES.map((l) => (
            <option key={l.code} value={l.code}>{t(l.i18nKey)}</option>
          ))}
        </select>
      </div>

      {/* 通用配置卡片（管理员图形化修改，DB 覆盖 env，带说明文案） */}
      <ConfigGroupCard groupKey="embedding" />
      <ConfigGroupCard groupKey="runtime" />

      {/* 平台赠送额度 */}
      <div>
        <label className="block text-sm font-medium mb-1 text-textSecondary">{t('admin.defaultPlatformCredit')}</label>
        <p className="text-xs text-textMuted mb-2">{t('admin.defaultPlatformCreditDesc')}</p>
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={platformCredit}
            onChange={(e) => setPlatformCredit(parseInt(e.target.value) || 0)}
            min={0}
            max={999999}
            disabled={platformCredit > 0 && !hasActiveKeys}
            className="w-32 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50 disabled:opacity-40"
          />
          <button
            onClick={handlePlatformCreditSave}
            disabled={saving || platformCredit === (config?.default_platform_credit || 0)}
            className="btn btn-sm btn-primary"
          >
            {t('settings.save')}
          </button>
        </div>
        {!hasActiveKeys && (
          <p className="text-xs text-accent-400 mt-1.5">{t('admin.platformCreditNoActiveKey')}</p>
        )}
      </div>

      {/* 用户默认文件配额 */}
      <div>
        <label className="block text-sm font-medium mb-1 text-textSecondary">{t('admin.defaultFileQuota')}</label>
        <p className="text-xs text-textMuted mb-2">{t('admin.defaultFileQuotaDesc')}</p>
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={fileQuota}
            onChange={(e) => setFileQuota(parseInt(e.target.value) || 1)}
            min={1}
            max={1048576}
            className="w-32 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
          />
          <span className="text-xs text-textMuted">MB</span>
          <button
            onClick={() => {
              const old = config?.default_file_quota_mb ?? 100
              if (fileQuota === old) return
              const delta = fileQuota - old
              const confirmed = confirm(
                (fileQuota > old
                  ? t('admin.fileQuotaIncreaseConfirm')
                  : t('admin.fileQuotaDecreaseConfirm')
                )
                  .replace('{old}', String(old))
                  .replace('{new}', String(fileQuota))
                  .replace('{delta}', (delta >= 0 ? '+' : '') + delta)
              )
              if (!confirmed) return
              handleSave('file_quota', fileQuota)
            }}
            disabled={saving || fileQuota === (config?.default_file_quota_mb ?? 100)}
            className="px-3 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm disabled:opacity-40 transition-colors"
          >
            {t('settings.save')}
          </button>
        </div>
      </div>

      {/* 单文件上传大小限制（运行时，重启后恢复 env 默认值） */}
      <div>
        <label className="block text-sm font-medium mb-1 text-textSecondary">单文件上传大小上限</label>
        <p className="text-xs text-textMuted mb-2">控制用户上传单个文件的最大尺寸（不含头像）</p>
        <div className="flex items-center gap-2">
          <input type="number" value={uploadMaxSizeMb}
            onChange={(e) => setUploadMaxSizeMb(parseInt(e.target.value) || 1)} min={1} max={1024}
            className="w-32 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
          <span className="text-xs text-textMuted">MB</span>
          <button
            onClick={async () => {
              setSaving(true)
              try {
                await api.put('/admin/upload-limits', { upload_max_size_mb: uploadMaxSizeMb })
                setMsg('上传大小限制已更新')
              } catch (e: any) { setMsg(e?.detail || '更新失败') }
              finally { setSaving(false) }
            }}
            disabled={saving}
            className="px-3 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm disabled:opacity-40 transition-colors"
          >{t('settings.save')}</button>
        </div>
      </div>

      {/* 头像上传大小限制（运行时，重启后恢复 env 默认值） */}
      <div>
        <label className="block text-sm font-medium mb-1 text-textSecondary">头像上传大小上限</label>
        <p className="text-xs text-textMuted mb-2">控制用户/AI 上传头像的最大尺寸</p>
        <div className="flex items-center gap-2">
          <input type="number" value={avatarMaxSizeMb}
            onChange={(e) => setAvatarMaxSizeMb(parseInt(e.target.value) || 1)} min={1} max={100}
            className="w-32 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
          <span className="text-xs text-textMuted">MB</span>
          <button
            onClick={async () => {
              setSaving(true)
              try {
                await api.put('/admin/upload-limits', { avatar_max_size_mb: avatarMaxSizeMb })
                setMsg('头像大小限制已更新')
              } catch (e: any) { setMsg(e?.detail || '更新失败') }
              finally { setSaving(false) }
            }}
            disabled={saving}
            className="px-3 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm disabled:opacity-40 transition-colors"
          >{t('settings.save')}</button>
        </div>
      </div>

      {/* 用户行为日志 */}
      <div>
        <div className="flex items-center justify-between">
          <div>
            <label className="text-sm font-medium text-textSecondary">用户行为日志</label>
            <p className="text-xs text-textMuted mt-0.5">记录用户登录、注册等行为（哈希链防篡改）</p>
          </div>
          <Toggle
            checked={auditUserActions}
            onChange={(val: boolean) => {
              setAuditUserActions(val)
              handleSave('audit_user_actions', val)
            }}
          />
        </div>
      </div>

      <div>
        <label className="text-sm font-medium text-textSecondary">审计日志保留天数</label>
        <p className="text-xs text-textMuted mt-0.5 mb-2">超期日志自动清理（7-730 天）</p>
        <div className="flex items-center gap-2">
          <input type="number" value={auditRetention}
            onChange={e => setAuditRetention(parseInt(e.target.value) || 90)} min={7} max={730}
            className="w-24 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
          <span className="text-xs text-textMuted">天</span>
          <button onClick={() => handleSave('audit_retention', auditRetention)}
            disabled={saving || auditRetention === (config?.audit_log_retention_days ?? 90)}
            className="px-3 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm disabled:opacity-40 transition-colors"
          >{t('settings.save')}</button>
        </div>
      </div>

      <div>
        <label className="text-sm font-medium text-textSecondary">消息保留天数</label>
        <p className="text-xs text-textMuted mt-0.5 mb-2">0=永久保留，超期消息自动删除</p>
        <div className="flex items-center gap-2">
          <input type="number" value={messageRetention}
            onChange={e => setMessageRetention(parseInt(e.target.value) || 0)} min={0} max={3650}
            className="w-24 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
          <span className="text-xs text-textMuted">天{messageRetention === 0 ? '（永久）' : ''}</span>
          <button onClick={() => handleSave('message_retention', messageRetention)}
            disabled={saving || messageRetention === (config?.message_retention_days ?? 0)}
            className="px-3 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm disabled:opacity-40 transition-colors"
          >{t('settings.save')}</button>
        </div>
      </div>

      {/* 每日数据库备份（管理员开关 + 保留份数） */}
      <div>
        <div className="flex items-center justify-between">
          <div>
            <label className="text-sm font-medium text-textSecondary">每日数据库备份</label>
            <p className="text-xs text-textMuted mt-0.5">开启后每天自动 pg_dump，备份文件保存在服务器 data/backups/</p>
          </div>
          <button onClick={() => handleSave('daily_backup_enabled', !dailyBackupEnabled)}
            disabled={saving}
            className={`relative w-11 h-6 rounded-full transition-colors ${dailyBackupEnabled ? 'bg-primary-500' : 'bg-gray-600'}`}
            title={t('settings.enable')}
          >
            <span className={`absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-all ${dailyBackupEnabled ? 'left-[22px]' : 'left-0.5'}`} />
          </button>
        </div>
        <div className="flex items-center gap-2 mt-2">
          <input type="number" value={dailyBackupKeep}
            onChange={e => setDailyBackupKeep(parseInt(e.target.value) || 7)} min={1} max={365}
            className="w-24 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
          <span className="text-xs text-textMuted">份（超出自动清除最旧备份）</span>
          <button onClick={() => handleSave('daily_backup_keep', dailyBackupKeep)}
            disabled={saving || dailyBackupKeep === (config?.daily_backup_keep ?? 7)}
            className="px-3 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm disabled:opacity-40 transition-colors"
          >{t('settings.save')}</button>
        </div>
      </div>

      <hr className="border-border" />

      {/* 注册通道开关 */}
      <div>
        <div className="flex items-center justify-between">
          <div>
            <label className="text-sm font-medium text-textSecondary">{t('admin.registrationEnabled')}</label>
            <p className="text-xs text-textMuted mt-0.5">{t('admin.registrationEnabledDesc')}</p>
          </div>
          <Toggle
            checked={registrationEnabled}
            onChange={(val: boolean) => {
              if (regToggleRef.current) return
              regToggleRef.current = true
              setRegistrationEnabled(val)
              handleSave('registration_enabled', val).finally(() => { regToggleRef.current = false })
            }}
          />
        </div>
      </div>

      {/* IP 地理位置查询后端 */}
      <div>
        <label className="text-sm font-medium text-textSecondary">IP 地理位置查询后端</label>
        <p className="text-xs text-textMuted mt-0.5 mb-2">{'支持 {ip} 占位符，留空默认 ip-api.com'}</p>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={geoipUrl}
            onChange={e => setGeoipUrl(e.target.value)}
            placeholder="http://ip-api.com/json/{ip}?fields=..."
            className="flex-1 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
          />
          <button
            onClick={() => handleSave('geoip_url', geoipUrl)}
            disabled={saving}
            className="px-3 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm disabled:opacity-40 transition-colors"
          >{t('settings.save')}</button>
        </div>
      </div>

      <hr className="border-border" />

      {/* 新建群聊默认 AI 并发数 */}
      <div>
        <label className="block text-sm font-medium mb-1 text-textSecondary">新建群聊默认 AI 并发数</label>
        <p className="text-xs text-textMuted mb-2">新建群聊时自动使用的 AI 并发上限（1-20）</p>
        <div className="flex items-center gap-2">
          <input type="number" value={defaultConcurrentAiLimit}
            onChange={(e) => setDefaultConcurrentAiLimit(parseInt(e.target.value) || 3)} min={1} max={20}
            className="w-32 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
          <button
            onClick={() => handleSave('concurrent_ai_limit', defaultConcurrentAiLimit)}
            disabled={saving || defaultConcurrentAiLimit === (config?.default_concurrent_ai_limit ?? 3)}
            className="px-3 py-2 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm disabled:opacity-40 transition-colors"
          >{t('settings.save')}</button>
        </div>
      </div>

      {/* 批量修改所有群并发数 */}
      <div>
        <label className="block text-sm font-medium mb-1 text-textSecondary">批量修改所有群并发数</label>
        <p className="text-xs text-textMuted mb-2">将已有全部群聊的 AI 并发上限设为同一值（覆盖已有设置）</p>
        <div className="flex items-center gap-2">
          <input type="number" value={bulkConcurrency}
            onChange={(e) => setBulkConcurrency(parseInt(e.target.value) || 3)} min={1} max={20}
            className="w-32 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50" />
          <button
            onClick={async () => {
              if (!confirm(`确定将所有群的 AI 并发数设为 ${bulkConcurrency}？此操作不可撤销。`)) return
              setBulking(true)
              try {
                await api.put('/admin/groups/concurrency', { concurrent_ai_limit: bulkConcurrency })
                setMsg(`已将所有群并发数设为 ${bulkConcurrency}`)
              } catch (e: any) { setMsg(e?.detail || '批量修改失败') }
              finally { setBulking(false) }
            }}
            disabled={bulking}
            className="px-3 py-2 bg-accent-500 text-white rounded-card hover:bg-accent-400 text-sm disabled:opacity-40 transition-colors"
          >{bulking ? '执行中...' : '批量应用'}</button>
        </div>
      </div>

      {msg && <p className={`text-sm ${msg.includes('失败') || msg.includes('无法') || msg.includes('No active') ? 'text-rose-400' : 'text-mint-400'}`}>{msg}</p>}
    </div>
  )
}
