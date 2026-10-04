import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { X, Bell, BellOff, Download, Clock, ArrowLeft, Pin, Heart, PinOff, HeartOff } from 'lucide-react'
import Toggle from './Toggle'

interface Partner {
  id: number
  name: string
  type: string
  state: string | null
}

interface Props {
  sessionId: string
  partner: Partner | null
  myDndUntil: string | null
  onClose: () => void
  onDndChange: (dndUntil: string | null) => void
}

const DND_DURATIONS = [
  { key: 'dmSettings:dnd15min', minutes: 15 },
  { key: 'dmSettings:dnd30min', minutes: 30 },
  { key: 'dmSettings:dnd1hour', minutes: 60 },
  { key: 'dmSettings:dnd4hours', minutes: 240 },
  { key: 'dmSettings:dnd8hours', minutes: 480 },
  { key: 'dmSettings:dndForever', minutes: null as unknown as number },
]

export default function DMSettingsPanel({ sessionId, partner, myDndUntil, onClose, onDndChange }: Props) {
  const t = useT()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [customMinutes, setCustomMinutes] = useState('')
  const [pinned, setPinned] = useState(false)
  const [specialCare, setSpecialCare] = useState(false)

  // 加载时从 localStorage / 缓存查询置顶状态
  // 更可靠的做法：从 DM sessions API 获取
  useEffect(() => {
    const loadPrefs = async () => {
      try {
        const sessions = await api.get('/dm/sessions')
        const s = (sessions as any[]).find((x: any) => x.session_id === sessionId)
        if (s) {
          setPinned(s.is_pinned ?? false)
          setSpecialCare(s.is_special_care ?? false)
        }
      } catch { /* ignore */ }
    }
    loadPrefs()
  }, [sessionId])

  // 导出状态
  const [exportFormat, setExportFormat] = useState('txt')
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')

  const handleSetDnd = async (minutes: number | null) => {
    if (!sessionId) return
    setLoading(true)
    setError('')
    try {
      await api.post(`/dm/${sessionId}/dnd`, { duration_minutes: minutes })
      const until = minutes === null ? 'permanent' : new Date(Date.now() + minutes * 60_000).toISOString()
      onDndChange(until)
    } catch (e: any) {
      setError(e?.detail || t('error:dndSetFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleCustomDnd = async () => {
    const mins = parseInt(customMinutes, 10)
    if (isNaN(mins) || mins <= 0) {
      setError(t('error:invalidMinutes'))
      return
    }
    if (mins > 10080) {
      setError(t('error:dndMaxDuration'))
      return
    }
    await handleSetDnd(mins)
    setCustomMinutes('')
  }

  const handleCancelDnd = async () => {
    if (!sessionId) return
    setLoading(true)
    setError('')
    try {
      await api.post(`/dm/${sessionId}/dnd/cancel`)
      onDndChange(null)
    } catch (e: any) {
      setError(e?.detail || t('error:cancelFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleExportChat = async () => {
    if (!sessionId) return
    setExporting(true)
    setExportError('')
    try {
      const ext = exportFormat === 'txt' ? 'txt' : exportFormat === 'html' ? 'html' : 'json'
      await api.download(`/dm/${sessionId}/export?fmt=${exportFormat}`,
                         `dm_${partner?.name || sessionId}_${new Date().toISOString().slice(0, 10)}.${ext}`)
    } catch (e: any) {
      setExportError(e.message)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-modal flex justify-end">
      {/* 点击外部关闭（桌面端） */}
      <div className="absolute inset-0 bg-black/30 hidden md:block" onClick={onClose} />

      <div className="relative w-full md:w-80 max-w-full h-full bg-surface md:border-l border-border shadow-2xl flex flex-col animate-slide-in">
        {/* 头部 */}
        <div className="h-14 px-4 border-b border-border flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="icon-btn-sm md:hidden -ml-1 text-textSecondary"
            >
              <ArrowLeft size={20} />
            </button>
            <h2 className="font-semibold text-sm text-textPrimary">{t('dmSettings:title')}</h2>
          </div>
          <button onClick={onClose} className="icon-btn-sm text-textMuted hidden md:block">
            <X size={16} />
          </button>
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 pb-[var(--safe-bottom)] md:pb-4">
          {error && (
            <div className="text-xs text-rose-400 bg-rose-400/10 rounded-control px-3 py-2">{error}</div>
          )}

          {/* === 免打扰设置 === */}
          <div>
            <h3 className="text-sm font-medium text-textPrimary flex items-center gap-2 mb-3">
              <Bell size={14} className="text-textMuted" />
              {t('dmSettings:dnd')}
            </h3>

            {myDndUntil ? (
              <div className="space-y-3">
                <div className="bg-mint-400/10 text-mint-400 rounded-control px-3 py-2 text-xs flex items-center gap-2">
                  <BellOff size={14} />
                  {t('dmSettings:dndEnabled')}
                </div>
                <button
                  onClick={handleCancelDnd}
                  disabled={loading}
                  className="btn btn-md btn-primary w-full"
                >
                  {t('dmSettings:cancelDnd')}
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-textMuted">{t('dmSettings:dndHint')}</p>
                <div className="grid grid-cols-2 gap-2">
                  {DND_DURATIONS.map((d) => (
                    <button
                      key={d.key}
                      onClick={() => handleSetDnd(d.minutes)}
                      disabled={loading}
                      className="flex items-center justify-center gap-1.5 px-3 py-2.5 bg-elevated hover:bg-primary-500/10 hover:text-primary-400 text-textSecondary border border-border rounded-control text-xs font-medium transition-colors disabled:opacity-50"
                    >
                      <Clock size={12} />
                      {t(d.key)}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2 items-center">
                  <input
                    type="number"
                    value={customMinutes}
                    onChange={(e) => setCustomMinutes(e.target.value)}
                    placeholder={t('groupSettings:dndCustomPlaceholder')}
                    min={1}
                    max={10080}
                    disabled={loading}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleCustomDnd() }}
                    className="flex-1 bg-elevated border border-border rounded-control px-3 py-2 text-sm text-textPrimary outline-none focus:border-primary-400 placeholder:text-textMuted disabled:opacity-50"
                  />
                  <button
                    onClick={handleCustomDnd}
                    disabled={loading || !customMinutes.trim()}
                    className="btn btn-xs btn-primary shrink-0"
                  >
                    {t('common:set')}
                  </button>
                </div>
              </div>
            )}
          </div>

          <hr className="border-border" />

          {/* === 置顶开关 === */}
          <div>
            <h3 className="text-sm font-medium text-textPrimary flex items-center gap-2 mb-3">
              <Pin size={14} className="text-textMuted" />
              置顶聊天
            </h3>
            <div className="flex items-center justify-between">
              <div className="text-xs text-textMuted">置顶后出现在侧边栏顶部</div>
              <Toggle
                checked={pinned}
                onChange={async (next) => {
                  try {
                    await api.post(`/dm/${sessionId}/pin`, { is_pinned: next })
                    setPinned(next)
                    window.dispatchEvent(new CustomEvent('groupListRefresh'))
                    window.dispatchEvent(new CustomEvent('groupPinChanged', {
                      detail: { sessionId, isPinned: next },
                    }))
                  } catch { /* ignore */ }
                }}
              />
            </div>
          </div>

          {/* === 特别关心开关 === */}
          <div>
            <h3 className="text-sm font-medium text-textPrimary flex items-center gap-2 mb-3">
              <Heart size={14} className="text-rose-400" />
              特别关心
            </h3>
            <div className="flex items-center justify-between">
              <div className="text-xs text-textMuted">开启后消息会以特别方式提示</div>
              <Toggle
                checked={specialCare}
                onChange={async (next) => {
                  try {
                    await api.post(`/dm/${sessionId}/pin`, { is_pinned: pinned, is_special_care: next })
                    setSpecialCare(next)
                  } catch { /* ignore */ }
                }}
              />
            </div>
          </div>

          <hr className="border-border" />

          {/* === 导出记录 === */}
          <div>
            <h3 className="text-sm font-medium text-textPrimary flex items-center gap-2 mb-3">
              <Download size={14} className="text-textMuted" />
              {t('dmSettings:exportChat')}
            </h3>

            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-textSecondary">{t('groupSettings:exportFormat')}</label>
                <div className="flex gap-2 mt-1">
                  {[
                    { key: 'json', labelKey: 'JSON' },
                    { key: 'txt', labelKey: 'TXT' },
                    { key: 'html', labelKey: 'HTML' },
                  ].map(f => (
                    <button
                      key={f.key}
                      onClick={() => setExportFormat(f.key)}
                      className={`btn btn-sm btn-outline flex-1 ${exportFormat === f.key ? 'is-active' : ''}`}
                    >
                      {f.labelKey}
                    </button>
                  ))}
                </div>
              </div>

              <button
                onClick={handleExportChat}
                disabled={exporting}
                className="btn btn-md btn-primary w-full gap-2"
              >
                <Download size={16} />
                {exporting ? t('common:exporting') : t('dmSettings:downloadExport')}
              </button>

              {exportError && <div className="text-xs text-rose-400">{exportError}</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
