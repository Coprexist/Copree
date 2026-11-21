import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, getApiBaseUrl } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { Button, HelpTip, Input, SecretInput} from './ui'

/**
 * 语音（TTS）面板（AI 详情页的一个页签）
 *
 * 与"配 LLM 的 API"是同一件事：选预设 → 填 key → 填模型/音色 → 保存。
 * 保存会顺手测一次（见 routers/agents.update_voice_settings），所以不用再记得点「测试」：
 * 任何字段一改，后端就把上次的检测结论作废，并在这次保存里把新结论测出来。
 *
 * 试听是唯一花钱的动作（真合成），测试不花：Breeze 走官方自检端点、
 * MiniMax 只发一条 max_tokens=1 的极小请求。
 */

interface VoicePreset {
  key: string
  label: string
  base_url: string
  api_key_url: string
  voice_id: string
  voices: { value: string; label: string }[]
  style_hint: boolean
}

interface VoiceConfig {
  provider: string
  base_url: string
  voice_id: string
  model: string
  has_key: boolean
  probe_ok: boolean
  probe_kind: string
  probe_message: string
  probed_at: string | null
}

interface Form {
  provider: string
  base_url: string
  api_key: string
  voice_id: string
  model: string
}

const EMPTY_FORM: Form = { provider: '', base_url: '', api_key: '', voice_id: '', model: '' }

function errorOf(e: unknown): string {
  const any = e as { detail?: string; message?: string }
  return any?.detail || any?.message || String(e)
}

export default function AgentVoicePanel({ agentId }: { agentId: number }) {
  const t = useT()
  const [presets, setPresets] = useState<VoicePreset[]>([])
  const [cfg, setCfg] = useState<VoiceConfig | null>(null)
  const [form, setForm] = useState<Form>(EMPTY_FORM)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [audioUrl, setAudioUrl] = useState('')
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const preset = useMemo(
    () => presets.find((p) => p.key === form.provider) || null,
    [presets, form.provider],
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, current] = await Promise.all([
        api.get<VoicePreset[]>('/agents/voice-presets'),
        api.get<VoiceConfig>(`/agents/${agentId}/voice`),
      ])
      setPresets(Array.isArray(list) ? list : [])
      setCfg(current)
      setForm({
        provider: current.provider || '',
        base_url: current.base_url || '',
        api_key: '',
        voice_id: current.voice_id || '',
        model: current.model || '',
      })
    } catch (e) {
      setNotice({ ok: false, text: errorOf(e) })
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => { void load() }, [load])
  useEffect(() => () => { if (audioUrl) URL.revokeObjectURL(audioUrl) }, [audioUrl])

  const applyPreset = (key: string) => {
    const p = presets.find((x) => x.key === key)
    setForm((f) => ({
      ...f,
      provider: key,
      base_url: p ? p.base_url : f.base_url,
      // 预设带了默认音色就顺手填上；用户随时可以改
      voice_id: p && p.voice_id ? p.voice_id : f.voice_id,
    }))
  }

  const payload = () => ({
    provider: form.provider,
    base_url: form.base_url.trim(),
    // 空字符串 = 不改这一项（后端按 None 处理），否则保存一次就把 key 洗掉了
    api_key: form.api_key.trim() ? form.api_key.trim() : null,
    voice_id: form.voice_id.trim(),
    model: form.model.trim(),
  })

  const absorb = (next: VoiceConfig) => {
    setCfg(next)
    setForm((f) => ({ ...f, api_key: '' }))
  }

  const save = async () => {
    setSaving(true)
    setNotice(null)
    try {
      const next = await api.put<VoiceConfig>(`/agents/${agentId}/voice`, payload())
      absorb(next)
      // 保存时后端顺手测过了：结论照「测试」那一套口径说出来
      setNotice(next.probed_at
        ? {
            ok: next.probe_ok,
            text: next.probe_message || (next.probe_ok
              ? t('agentDetail:voiceProbeOk')
              : t('agentDetail:voiceProbeFail')),
          }
        : { ok: true, text: t('agentDetail:voiceSaved') })
    } catch (e) {
      setNotice({ ok: false, text: errorOf(e) })
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setNotice(null)
    try {
      const next = await api.post<VoiceConfig>(`/agents/${agentId}/voice/test`, payload())
      absorb(next)
      setNotice({
        ok: next.probe_ok,
        text: next.probe_message || (next.probe_ok ? t('agentDetail:voiceProbeOk') : t('agentDetail:voiceProbeFail')),
      })
    } catch (e) {
      setNotice({ ok: false, text: errorOf(e) })
    } finally {
      setTesting(false)
    }
  }

  const preview = async () => {
    setPreviewing(true)
    setNotice(null)
    try {
      const token = localStorage.getItem('access_token') || ''
      const res = await fetch(`${getApiBaseUrl()}/agents/${agentId}/voice/preview`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) {
        let detail = `HTTP ${res.status}`
        try {
          const body = await res.json()
          detail = body?.detail || detail
        } catch {
          /* 非 JSON 错误体：保留状态码 */
        }
        throw new Error(detail)
      }
      const blob = await res.blob()
      if (audioUrl) URL.revokeObjectURL(audioUrl)
      setAudioUrl(URL.createObjectURL(blob))
      // 用户点了"试听"就是要听，直接放
      setTimeout(() => { void audioRef.current?.play().catch(() => undefined) }, 50)
    } catch (e) {
      setNotice({ ok: false, text: errorOf(e) })
    } finally {
      setPreviewing(false)
    }
  }

  if (loading) {
    return <div className="text-sm text-textMuted">{t('agentDetail:voiceNeverProbed')}</div>
  }

  const probeState = cfg?.probed_at
    ? (cfg.probe_ok
        ? <span className="chip chip-mint">{t('agentDetail:voiceProbeOk')}</span>
        : <span className="chip chip-rose">{t('agentDetail:voiceProbeFail')}</span>)
    : <span className="chip chip-muted">{t('agentDetail:voiceNeverProbed')}</span>

  return (
    <div className="card space-y-4 p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-base font-semibold text-textPrimary">{t('agentDetail:voiceSection')}</h3>
        <HelpTip content={t('agentDetail:voiceDesc')} />
        {probeState}
      </div>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <label className="block text-sm font-medium text-textSecondary">
            {t('agentDetail:voiceProvider')}
          </label>
          <select
            className="field w-full"
            value={form.provider}
            onChange={(e) => applyPreset(e.target.value)}
          >
            <option value="">{t('agentDetail:voiceNoPreset')}</option>
            {presets.map((p) => (
              <option key={p.key} value={p.key}>{p.label}</option>
            ))}
          </select>
        </div>

        <Input
          label={t('agentDetail:voiceBaseUrl')}
          value={form.base_url}
          onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
          placeholder="https://api.example.com/v1"
        />

        <SecretInput
          label={t('agentDetail:voiceApiKey')}
          value={form.api_key}
          onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
          placeholder={cfg?.has_key
            ? t('agentDetail:voiceApiKeyKeep')
            : t('agentDetail:voiceApiKeyPlaceholder')}
        />

        <Input
          label={t('agentDetail:voiceId')}
          value={form.voice_id}
          onChange={(e) => setForm((f) => ({ ...f, voice_id: e.target.value }))}
          placeholder={t('agentDetail:voiceIdPlaceholder')}
        />
        {preset && preset.voices.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-textMuted">{t('agentDetail:voicePresetAutoVoice')}</span>
            {preset.voices.map((v) => (
              <button
                key={v.value}
                type="button"
                className="chip"
                onClick={() => setForm((f) => ({ ...f, voice_id: v.value }))}
              >
                {v.label}
              </button>
            ))}
          </div>
        )}

        <Input
          label={t('agentDetail:voiceModel')}
          value={form.model}
          onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
          placeholder={t('agentDetail:voiceModelPlaceholder')}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => void save()} disabled={saving}>
          {saving ? t('agentDetail:voiceTesting') : t('agentDetail:voiceSave')}
        </Button>
        <Button variant="secondary" onClick={() => void test()} disabled={testing}>
          {testing ? t('agentDetail:voiceTesting') : t('agentDetail:voiceTest')}
        </Button>
        <Button variant="ghost" onClick={() => void preview()} disabled={previewing || !cfg?.base_url}>
          {previewing ? t('agentDetail:voicePreviewing') : t('agentDetail:voicePreview')}
        </Button>
        <HelpTip content={t('agentDetail:voiceProbeHint')} />
      </div>

      {notice && (
        <div className={`text-sm ${notice.ok ? 'text-mint-400' : 'text-rose-400'}`}>{notice.text}</div>
      )}

      {audioUrl && (
        <audio ref={audioRef} src={audioUrl} controls className="w-full" />
      )}
    </div>
  )
}
