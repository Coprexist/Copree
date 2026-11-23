/**
 * 编辑「一条配置档」—— 这套配置叫什么、用哪把 Key、覆盖哪些模型。
 *
 * 这里只放覆盖位，不放厂商字段：地址与模型清单在 ProviderEditor（那边点进去改）。
 * 留空 = 用所属厂商的默认值，界面上把生效值直接写在提示里，不用再猜"空是什么意思"。
 */
import { useEffect, useMemo, useState } from 'react'
import { api, errorText } from '../../api/client'
import { useT } from '../../i18n/I18nContext'
import { Button, Input, Modal, SecretInput, Select } from '../ui'
import type { ApiKind, ApiProvider } from './types'

export interface ProfileDraft {
  id?: number
  providerId?: number
  name: string
  apiKey: string
  keyTouched: boolean
  primary: string
  secondary: string
}

export const EMPTY_DRAFT: ProfileDraft = {
  providerId: undefined,
  name: '',
  apiKey: '',
  keyTouched: false,
  primary: '',
  secondary: '',
}

export default function ProfileEditor({
  open,
  kind,
  providers,
  profileId,
  defaultProviderId,
  draft,
  onDraft,
  onNeedProvider,
  onClose,
  onSaved,
}: {
  open: boolean
  kind: ApiKind
  providers: ApiProvider[]
  profileId?: number
  defaultProviderId?: number
  draft?: ProfileDraft
  onDraft?: (d: ProfileDraft) => void
  onNeedProvider?: () => void
  onClose: () => void
  onSaved: () => void
}) {
  const t = useT()
  const [form, setForm] = useState<ProfileDraft>(draft || EMPTY_DRAFT)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // 打开时定位到要编辑的那一条；draft 存在说明是"去接入厂商"回来接着填的
  useEffect(() => {
    if (!open) return
    setError('')
    if (draft) {
      setForm(draft)
      return
    }
    const found = profileId
      ? providers.flatMap((p) => p.profiles.map((x) => ({ p, x }))).find((r) => r.x.id === profileId)
      : undefined
    setForm({
      ...EMPTY_DRAFT,
      id: found?.x.id,
      providerId: found?.p.id ?? defaultProviderId ?? providers[0]?.id,
      name: found?.x.name || '',
      primary: found?.x.primary_model || '',
      secondary: found?.x.secondary_model || '',
    })
  }, [open, profileId, defaultProviderId, draft, providers])

  const update = (patch: Partial<ProfileDraft>) => {
    setForm((prev) => {
      const next = { ...prev, ...patch }
      onDraft?.(next)
      return next
    })
  }

  const provider = useMemo(
    () => providers.find((p) => p.id === form.providerId),
    [providers, form.providerId],
  )
  const isVoice = kind === 'voice'
  const modelOptions = (provider?.options || []).filter((o) => o.value)

  const save = async () => {
    if (!form.name.trim()) {
      setError(t('apiConfig:nameRequired'))
      return
    }
    if (!form.providerId) {
      setError(t('apiConfig:emptyTitle'))
      return
    }
    setSaving(true)
    setError('')
    const body: Record<string, unknown> = {
      provider_id: form.providerId,
      name: form.name.trim(),
      primary_model: form.primary.trim() || null,
      secondary_model: form.secondary.trim() || null,
    }
    if (form.keyTouched) body.api_key = form.apiKey
    try {
      if (form.id) await api.put('/user/api-config/profiles/' + form.id, body)
      else await api.post('/user/api-config/profiles', body)
      onSaved()
      onClose()
    } catch (e) {
      setError(errorText(e, t('apiConfig:loadFailed')))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={form.id ? t('apiConfig:profileEditTitle') : t('apiConfig:profileNewTitle')}
      bodyClassName="min-h-[16rem]"
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>{t('apiConfig:cancel')}</Button>
          <Button variant="primary" size="sm" loading={saving} onClick={save}>{t('apiConfig:save')}</Button>
        </div>
      }
    >
      <div className="space-y-3">
        {error && <p className="text-xs text-rose-400">{error}</p>}

        <Input label={t('apiConfig:profileName')} fieldSize="sm"
          placeholder={t('apiConfig:profileNamePlaceholder')}
          value={form.name} onChange={(e) => update({ name: e.target.value })} />

        {/* 厂商用胶囊单选，不用下拉：下拉的选项列表由系统画（各平台一个样、也改不了样式），
            而这里要的是"一眼看完已接入的几家"，跟平台其他"设置值用胶囊"的规范也一致 */}
        <div>
          <span className="text-xs font-medium text-textSecondary">{t('apiConfig:profileProvider')}</span>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {providers.map((p) => (
              <Button key={p.id} size="sm" variant="secondary"
                className={form.providerId === p.id ? 'is-active' : ''}
                onClick={() => update({ providerId: p.id, primary: '', secondary: '' })}>
                {p.name}
              </Button>
            ))}
            {providers.length === 0 && (
              <span className="text-3xs text-textMuted">{t('apiConfig:emptyTitle')}</span>
            )}
            {onNeedProvider && (
              <Button variant="ghost" size="sm" onClick={onNeedProvider}>
                {t('apiConfig:connect')}
              </Button>
            )}
          </div>
        </div>

        <SecretInput
          label={t('apiConfig:profileKey')}
          fieldSize="sm"
          value={form.apiKey}
          onChange={(e) => update({ apiKey: e.target.value, keyTouched: true })}
          hint={provider?.has_key
            ? t('apiConfig:profileKeyHint', { last4: provider.key_last4 })
            : t('apiConfig:profileKeyMissing')}
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <Select
            label={isVoice ? t('apiConfig:profileVoice') : t('apiConfig:profilePrimary')}
            fieldSize="sm"
            placeholder={provider?.default_primary
              ? t('apiConfig:profilePrimaryHint', { model: provider.default_primary })
              : undefined}
            options={modelOptions.map((o) => ({ value: o.value, label: o.label || o.value }))}
            value={form.primary}
            onChange={(e) => update({ primary: e.target.value })}
          />
          {!isVoice && (
            <Select
              label={t('apiConfig:profileSecondary')}
              fieldSize="sm"
              placeholder={provider?.default_secondary
                ? t('apiConfig:profileSecondaryHint', { model: provider.default_secondary })
                : undefined}
              options={modelOptions.map((o) => ({ value: o.value, label: o.label || o.value }))}
              value={form.secondary}
              onChange={(e) => update({ secondary: e.target.value })}
            />
          )}
        </div>
      </div>
    </Modal>
  )
}
