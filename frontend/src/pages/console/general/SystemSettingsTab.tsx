// 平台设置：站点 / 注册 / 语言等分组配置
//
// 这里只写语义：卡片用 Card（标题 / 说明 / 右上动作），控件用 Input / Select / Toggle / Button。
// 手写 div.card + 一长串 Tailwind 的写法已经收掉——视觉来源是 .card / .field / .btn 三个语义类。

import { useState, useEffect, useRef } from 'react'
import { api } from '../../../api/client'
import Toggle from '../../../components/Toggle'
import { useT } from '../../../i18n/I18nContext'
import { LANGUAGES } from '../../../i18n/languages'
import UpdateCard from '../../../components/UpdateCard'
import { Button, Card, Input, Select } from '../../../components/ui'
import { NOTICE_TEXT_CLASS, useNotice } from '../../../hooks/useNotice'

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
  const [auditRealIp, setAuditRealIp] = useState(false)
  const [auditRetention, setAuditRetention] = useState(90)
  const [messageRetention, setMessageRetention] = useState(0)
  const [dailyBackupEnabled, setDailyBackupEnabled] = useState(false)
  const [dailyBackupKeep, setDailyBackupKeep] = useState(7)
  // 容量策略：0 = 不设（走默认调控）。口径是"这个服务占多少内存"（容器 cgroup 用量）
  const [memoryDailyTarget, setMemoryDailyTarget] = useState(0)
  const [memoryLimit, setMemoryLimit] = useState(0)
  const [geoipUrl, setGeoipUrl] = useState('')
  const [bulkConcurrency, setBulkConcurrency] = useState(3)
  const [bulking, setBulking] = useState(false)
  const [hasActiveKeys, setHasActiveKeys] = useState(false)
  const [saving, setSaving] = useState(false)
  const { notice, ok: noticeOk, fail: noticeFail, clear: clearNotice } = useNotice()

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
      setDefaultConcurrentAiLimit(settings.default_concurrent_ai_limit ?? 2)
      setRegistrationEnabled(settings.registration_enabled ?? true)
      setAuditUserActions(settings.audit_user_actions ?? false)
      setAuditRealIp(settings.audit_use_real_ip ?? false)
      setAuditRetention(settings.audit_log_retention_days ?? 90)
      setMessageRetention(settings.message_retention_days ?? 0)
      setDailyBackupEnabled(settings.daily_backup_enabled ?? false)
      setDailyBackupKeep(settings.daily_backup_keep ?? 7)
      setMemoryDailyTarget(settings.memory_daily_target_mb ?? 0)
      setMemoryLimit(settings.memory_limit_mb ?? 0)
      setGeoipUrl(settings.geoip_provider_url || '')
      setHasActiveKeys(keys.some((k: any) => k.is_active))
    }).catch(console.error)
  }, [])

  const handleSave = async (field: string, value: any) => {
    setSaving(true)
    clearNotice()
    try {
      const payload: any = {}
      if (field === 'language') payload.default_language = value
      else if (field === 'platform_credit') payload.default_platform_credit = value
      else if (field === 'file_quota') payload.default_file_quota_mb = value
      else if (field === 'concurrent_ai_limit') payload.default_concurrent_ai_limit = value
      else if (field === 'registration_enabled') payload.registration_enabled = value
      else if (field === 'audit_user_actions') payload.audit_user_actions = value
      else if (field === 'audit_use_real_ip') payload.audit_use_real_ip = value
      else if (field === 'audit_retention') payload.audit_log_retention_days = value
      else if (field === 'message_retention') payload.message_retention_days = value
      else if (field === 'daily_backup_enabled') payload.daily_backup_enabled = value
      else if (field === 'daily_backup_keep') payload.daily_backup_keep = value
      else if (field === 'geoip_url') payload.geoip_provider_url = value || null
      else if (field === 'memory_policy') {
        payload.memory_daily_target_mb = value.daily
        payload.memory_limit_mb = value.limit
      }
      const updated = await api.put('/admin/system-settings', payload)
      setConfig(updated)
      noticeOk(t('admin:saveSuccess'))
    } catch (err: any) {
      noticeFail(err?.message || err?.detail || t('admin:saveFailed'))
    }
    setSaving(false)
  }

  const handlePlatformCreditSave = () => {
    const old = config?.default_platform_credit || 0
    if (platformCredit === old) return
    if (platformCredit > 0 && !hasActiveKeys) {
      noticeFail(t('admin:platformCreditNoActiveKey'))
      return
    }
    const delta = platformCredit - old
    const confirmed = confirm(
      t('admin:platformCreditConfirm')
        .replace('{old}', String(old))
        .replace('{new}', String(platformCredit))
        .replace('{delta}', (delta >= 0 ? '+' : '') + delta)
        .replace('{userCount}', t('admin:allUsers'))
    )
    if (!confirmed) return
    handleSave('platform_credit', platformCredit)
  }

  if (!config) return <p className="text-textMuted p-6">{t('common:loading')}</p>

  const saveOff = saving

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <h3 className="xl:col-span-2 font-semibold text-textPrimary">{t('admin:systemSettings')}</h3>

      {/* 版本与更新：放最上面——升级提示不该翻到卡片列表底部才看见 */}
      <UpdateCard className="xl:col-span-2" />

      {/* 默认语言 */}
      <Card title={t('admin:defaultLanguage')} hint={t('admin:defaultLanguageDesc')}>
        <Select
          value={lang}
          onChange={(e) => {
            setLang(e.target.value)
            handleSave('language', e.target.value)
          }}
          options={LANGUAGES.map((l) => ({ value: l.code, label: t(l.i18nKey) }))}
        />
      </Card>

      {/* 注册通道开关 */}
      <Card
        title={t('admin:registrationEnabled')}
        hint={t('admin:registrationEnabledDesc')}
        action={
          <Toggle
            checked={registrationEnabled}
            onChange={(val: boolean) => {
              if (regToggleRef.current) return
              regToggleRef.current = true
              setRegistrationEnabled(val)
              handleSave('registration_enabled', val).finally(() => { regToggleRef.current = false })
            }}
          />
        }
      />

      {/* 平台赠送额度 */}
      <Card title={t('admin:defaultPlatformCredit')} hint={t('admin:defaultPlatformCreditDesc')}>
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={platformCredit}
            onChange={(e) => setPlatformCredit(parseInt(e.target.value) || 0)}
            min={0}
            max={999999}
            disabled={platformCredit > 0 && !hasActiveKeys}
          />
          <Button
            size="sm"
            variant="primary"
            onClick={handlePlatformCreditSave}
            disabled={saveOff || platformCredit === (config?.default_platform_credit || 0)}
          >
            {t('settings:save')}
          </Button>
        </div>
        {!hasActiveKeys && (
          <p className="text-xs text-accent-400">{t('admin:platformCreditNoActiveKey')}</p>
        )}
      </Card>

      {/* 用户默认文件配额 */}
      <Card title={t('admin:defaultFileQuota')} hint={t('admin:defaultFileQuotaDesc')}>
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={fileQuota}
            onChange={(e) => setFileQuota(parseInt(e.target.value) || 1)}
            min={1}
            max={1048576}
          />
          <span className="text-xs text-textMuted">MB</span>
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              const old = config?.default_file_quota_mb ?? 100
              if (fileQuota === old) return
              const delta = fileQuota - old
              const confirmed = confirm(
                (fileQuota > old
                  ? t('admin:fileQuotaIncreaseConfirm')
                  : t('admin:fileQuotaDecreaseConfirm')
                )
                  .replace('{old}', String(old))
                  .replace('{new}', String(fileQuota))
                  .replace('{delta}', (delta >= 0 ? '+' : '') + delta)
              )
              if (!confirmed) return
              handleSave('file_quota', fileQuota)
            }}
            disabled={saveOff || fileQuota === (config?.default_file_quota_mb ?? 100)}
          >
            {t('settings:save')}
          </Button>
        </div>
      </Card>

      {/* 单文件上传大小限制（落 runtime 配置组：热生效、重启不丢） */}
      <Card title="单文件上传大小上限" hint="控制用户上传单个文件的最大尺寸（不含头像）">
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={uploadMaxSizeMb}
            onChange={(e) => setUploadMaxSizeMb(parseInt(e.target.value) || 1)}
            min={1}
            max={1024}
          />
          <span className="text-xs text-textMuted">MB</span>
          <Button
            size="sm"
            variant="primary"
            disabled={saveOff}
            onClick={async () => {
              setSaving(true)
              try {
                await api.put('/admin/upload-limits', { upload_max_size_mb: uploadMaxSizeMb })
                noticeOk(t('admin:uploadLimitUpdated'))
              } catch (e: any) { noticeFail(e?.detail || t('admin:updateFailed')) }
              finally { setSaving(false) }
            }}
          >
            {t('settings:save')}
          </Button>
        </div>
      </Card>

      {/* 头像上传大小限制（落 runtime 配置组：热生效、重启不丢） */}
      <Card title="头像上传大小上限" hint="控制用户/AI 上传头像的最大尺寸">
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={avatarMaxSizeMb}
            onChange={(e) => setAvatarMaxSizeMb(parseInt(e.target.value) || 1)}
            min={1}
            max={100}
          />
          <span className="text-xs text-textMuted">MB</span>
          <Button
            size="sm"
            variant="primary"
            disabled={saveOff}
            onClick={async () => {
              setSaving(true)
              try {
                await api.put('/admin/upload-limits', { avatar_max_size_mb: avatarMaxSizeMb })
                noticeOk(t('admin:avatarLimitUpdated'))
              } catch (e: any) { noticeFail(e?.detail || t('admin:updateFailed')) }
              finally { setSaving(false) }
            }}
          >
            {t('settings:save')}
          </Button>
        </div>
      </Card>

      {/* 用户行为日志 */}
      <Card
        title="用户行为日志"
        hint="记录用户登录、注册等行为（哈希链防篡改）"
        action={
          <Toggle
            checked={auditUserActions}
            onChange={(val: boolean) => {
              setAuditUserActions(val)
              handleSave('audit_user_actions', val)
            }}
          />
        }
      />

      {/* 真实客户端 IP */}
      <Card
        title="记录真实客户端 IP"
        hint="开启后审计日志记录经反向代理透传的访客真实 IP；关闭则记录代理地址（无意义）。需部署层传入 X-Real-IP，且仅信任 TRUSTED_PROXY_CIDRS 内的对端。"
        action={
          <Toggle
            checked={auditRealIp}
            onChange={(val: boolean) => {
              setAuditRealIp(val)
              handleSave('audit_use_real_ip', val)
            }}
          />
        }
      />

      {/* 审计日志保留天数 */}
      <Card title="审计日志保留天数" hint="超期日志自动清理（7-730 天）">
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={auditRetention}
            onChange={(e) => setAuditRetention(parseInt(e.target.value) || 90)}
            min={7}
            max={730}
          />
          <span className="text-xs text-textMuted">天</span>
          <Button
            size="sm"
            variant="primary"
            disabled={saveOff || auditRetention === (config?.audit_log_retention_days ?? 90)}
            onClick={() => handleSave('audit_retention', auditRetention)}
          >
            {t('settings:save')}
          </Button>
        </div>
      </Card>

      {/* 消息保留天数 */}
      <Card title="消息保留天数" hint="0=永久保留，超期消息自动删除">
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={messageRetention}
            onChange={(e) => setMessageRetention(parseInt(e.target.value) || 0)}
            min={0}
            max={3650}
          />
          <span className="text-xs text-textMuted">天{messageRetention === 0 ? '（永久）' : ''}</span>
          <Button
            size="sm"
            variant="primary"
            disabled={saveOff || messageRetention === (config?.message_retention_days ?? 0)}
            onClick={() => handleSave('message_retention', messageRetention)}
          >
            {t('settings:save')}
          </Button>
        </div>
      </Card>

      {/* 每日数据库备份（管理员开关 + 保留份数） */}
      <Card
        title="每日数据库备份"
        hint="开启后每天自动 pg_dump，备份文件保存在服务器 data/backups/"
        action={
          <Toggle
            checked={dailyBackupEnabled}
            onChange={(val: boolean) => {
              setDailyBackupEnabled(val)
              handleSave('daily_backup_enabled', val)
            }}
          />
        }
      >
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={dailyBackupKeep}
            onChange={(e) => setDailyBackupKeep(parseInt(e.target.value) || 7)}
            min={1}
            max={365}
          />
          <span className="text-xs text-textMuted">份（超出自动清除最旧备份）</span>
          <Button
            size="sm"
            variant="primary"
            disabled={saveOff || dailyBackupKeep === (config?.daily_backup_keep ?? 7)}
            onClick={() => handleSave('daily_backup_keep', dailyBackupKeep)}
          >
            {t('settings:save')}
          </Button>
        </div>
      </Card>

      {/* 容量策略：管理员给的两条内存线（不填就走默认调控） */}
      <Card
        title="容量与内存策略"
        hint="并发过高时按这两条线自动收紧同时运行的对话。口径是本服务占用的内存（含子进程）；留 0 = 不设，走默认算法（事件循环延迟 / 数据库连接池 / 入口队列）。"
      >
        <div className="flex flex-wrap items-end gap-3">
          <Input
            label="日常内存占用"
            type="number"
            fieldSize="sm"
            className="w-28"
            value={memoryDailyTarget}
            min={0}
            onChange={(e) => setMemoryDailyTarget(Math.max(0, parseInt(e.target.value) || 0))}
          />
          <Input
            label="内存占用上限"
            type="number"
            fieldSize="sm"
            className="w-28"
            value={memoryLimit}
            min={0}
            onChange={(e) => setMemoryLimit(Math.max(0, parseInt(e.target.value) || 0))}
          />
          <Button
            size="sm"
            variant="primary"
            disabled={saveOff || (memoryDailyTarget === (config?.memory_daily_target_mb ?? 0)
              && memoryLimit === (config?.memory_limit_mb ?? 0))}
            onClick={() => handleSave('memory_policy', { daily: memoryDailyTarget, limit: memoryLimit })}
          >
            {t('settings:save')}
          </Button>
        </div>
        {memoryDailyTarget > 0 && memoryLimit > 0 && memoryDailyTarget > memoryLimit && (
          <p className="text-xs text-rose-400">日常占用不能高于上限</p>
        )}
      </Card>

      {/* IP 地理位置查询后端 */}
      <Card title="IP 地理位置查询后端" hint="支持 {ip} 占位符，留空默认 ip-api.com">
        <div className="flex items-center gap-2">
          <Input
            type="text"
            fieldSize="sm"
            className="flex-1 min-w-0"
            value={geoipUrl}
            onChange={(e) => setGeoipUrl(e.target.value)}
            placeholder="http://ip-api.com/json/{ip}?fields=..."
          />
          <Button
            size="sm"
            variant="primary"
            disabled={saveOff}
            onClick={() => handleSave('geoip_url', geoipUrl)}
          >
            {t('settings:save')}
          </Button>
        </div>
      </Card>

      {/* 新建群聊默认 AI 并发数 */}
      <Card title="新建群聊默认 AI 并发数" hint="新建群聊时自动使用的 AI 并发上限（1-20）">
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={defaultConcurrentAiLimit}
            onChange={(e) => setDefaultConcurrentAiLimit(parseInt(e.target.value) || 3)}
            min={1}
            max={20}
          />
          <Button
            size="sm"
            variant="primary"
            disabled={saveOff || defaultConcurrentAiLimit === (config?.default_concurrent_ai_limit ?? 2)}
            onClick={() => handleSave('concurrent_ai_limit', defaultConcurrentAiLimit)}
          >
            {t('settings:save')}
          </Button>
        </div>
      </Card>

      {/* 批量修改所有群并发数 */}
      <Card
        className="xl:col-span-2"
        title="批量修改所有群并发数"
        hint="将已有全部群聊的 AI 并发上限设为同一值（覆盖已有设置）"
      >
        <div className="flex items-center gap-2">
          <Input
            type="number"
            fieldSize="sm"
            className="w-28"
            value={bulkConcurrency}
            onChange={(e) => setBulkConcurrency(parseInt(e.target.value) || 3)}
            min={1}
            max={20}
          />
          <Button
            size="sm"
            variant="accent"
            disabled={bulking}
            onClick={async () => {
              if (!confirm(t('admin:bulkConcurrencyConfirm', { n: bulkConcurrency }))) return
              setBulking(true)
              try {
                await api.put('/admin/groups/concurrency', { concurrent_ai_limit: bulkConcurrency })
                noticeOk(t('admin:bulkConcurrencySet', { n: bulkConcurrency }))
              } catch (e: any) { noticeFail(e?.detail || t('admin:bulkUpdateFailed')) }
              finally { setBulking(false) }
            }}
          >
            {bulking ? t('admin:bulkApplyRunning') : t('admin:bulkApply')}
          </Button>
        </div>
      </Card>

      {notice && <p className={`xl:col-span-2 text-sm ${NOTICE_TEXT_CLASS[notice.tone]}`}>{notice.text}</p>}
    </div>
  )
}
