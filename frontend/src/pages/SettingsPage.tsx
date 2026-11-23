import { useState, useEffect, useRef } from 'react'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'
import { useT, useLang } from '../i18n/I18nContext'
import { fmtDateTime } from '../utils/time'
import Toggle from '../components/Toggle'
import { Slider } from '../components/ui'
import ApiConfigSection from '../components/api-config/ApiConfigSection'
import { IN_APP_NOTIFICATION_KEY } from '../hooks/useNotificationSocket'
import MagicVisionFilter from '../components/MagicVisionFilter.tsx'
import ThemeCustomizer from '../components/ThemeCustomizer'
import SkinPicker from '../components/SkinPicker'
import { defaultPrefs, loadFromStorage, saveToStorage, apply } from '../utils/cssFilters'
import type { MagicVisionPrefs } from '../utils/cssFilters'
import { useResizableSidebar } from '../hooks/useResizableSidebar'
import { useRailCollapsed, RAIL_COLLAPSED_WIDTH as RAIL_W } from '../hooks/useRailCollapsed'
import VerificationCodeInput from '../components/VerificationCodeInput'
import { LANGUAGES } from '../i18n/languages'
import { NOTICE_BOX_CLASS, NOTICE_TEXT_CLASS, useNotice } from '../hooks/useNotice'
import { isDesktop } from '../utils/platform'
import { invoke } from '../utils/tauri'
import { Key, Zap, Save, Clock, Palette, Bell, CheckCircle, XCircle, Loader2, Globe, Layout, Ticket, Plus, ChevronDown, ChevronRight, Shield, AlertTriangle, ArrowLeft, Mail, Monitor, HardDrive, Trash2, Cpu, Wrench, Box, ExternalLink, BarChart3, ChevronLeft } from 'lucide-react'
import { useNavigate, useBlocker, useLocation, useSearchParams } from 'react-router-dom'

// 常用时区列表
const TIMEZONES = [
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Seoul',
  'Asia/Singapore',
  'Asia/Kolkata',
  'Asia/Dubai',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Moscow',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Sao_Paulo',
  'Pacific/Auckland',
  'Australia/Sydney',
  'UTC',
]

const CHAT_STYLES = [
  { value: 'cozy', key: 'settings:cozyMode', descKey: 'settings:cozyModeDesc' },
  { value: 'compact', key: 'settings:compactMode', descKey: 'settings:compactModeDesc' },
]

/** 侧边栏导航项（含分类） */
interface NavSection {
  id: string
  icon: React.ElementType
  labelKey: string
  category: string
}
const NAV_SECTIONS: NavSection[] = [
  { id: 'quota',       icon: Zap,     labelKey: 'settings:quotaTitle',      category: 'settings:catAccount' },
  { id: 'email',       icon: Mail,    labelKey: 'auth:email',                category: 'settings:catAccount' },
  { id: 'api',         icon: Key,     labelKey: 'settings:apiConfigTitle',   category: 'settings:catApi' },
  { id: 'timezone',    icon: Clock,   labelKey: 'settings:timezone',         category: 'settings:catPrefs' },
  { id: 'language',    icon: Globe,   labelKey: 'settings:language',         category: 'settings:catPrefs' },
  { id: 'chatstyle',   icon: Layout,   labelKey: 'settings:chatStyle',       category: 'settings:catPrefs' },
  { id: 'uiscale',    icon: Monitor,  labelKey: 'settings:uiScale',           category: 'settings:catPrefs' },
  { id: 'mermaid',    icon: BarChart3, labelKey: 'settings:mermaidTitle',     category: 'settings:catPrefs' },
  { id: 'appearance',  icon: Palette, labelKey: 'settings:appearance',       category: 'settings:catPrefs' },
  { id: 'notifications', icon: Bell,  labelKey: 'settings:notifications',   category: 'settings:catPrefs' },
  { id: 'strategy',    icon: Zap,     labelKey: 'settings:strategy',         category: 'settings:catPrefs' },
  ...(isDesktop() ? [
    { id: 'desktop',  icon: Monitor, labelKey: 'settings:desktopSection',     category: 'settings:catDesktop' },
  ] : []),
]

export default function SettingsPage() {
  const t = useT()
  const lang = useLang()
  const { user, refreshUser } = useAuth()
  const { theme, toggleTheme } = useTheme()

  const getMermaidSetting = (key: string, fallback: boolean) => {
    try {
      return localStorage.getItem(key) === null ? fallback : localStorage.getItem(key) === 'true'
    } catch {
      return fallback
    }
  }
  const navigate = useNavigate()
  const [preferOwnKey, setPreferOwnKey] = useState(false)
  const [autoTimeout, setAutoTimeout] = useState(60)
  const [autoDefault, setAutoDefault] = useState(false)
  const [timezone, setTimezone] = useState('Asia/Shanghai')
  const [language, setLanguage] = useState('zh')
  const [magicVision, setMagicVision] = useState<MagicVisionPrefs>(() => loadFromStorage() || defaultPrefs())
  const [chatStyle, setChatStyle] = useState('cozy')
  const [uiScale, setUiScale] = useState(() => {
    try { return parseFloat(localStorage.getItem('ui_scale') || '1') } catch { return 1 }
  })


  // 应用 UI 缩放
  useEffect(() => {
    document.documentElement.style.fontSize = `${85 + (uiScale - 0.85) * 100}%`
  }, [uiScale])
  const [notifications, setNotifications] = useState<boolean>(() => {
    const stored = localStorage.getItem('notifications_enabled')
    return stored === null ? true : stored === 'true'
  })
  // 站内弹窗与系统通知是两件事（一个管看着主站时，一个管标签页在后台），开关也分开
  const [popupNotifications, setPopupNotifications] = useState<boolean>(() => {
    const stored = localStorage.getItem(IN_APP_NOTIFICATION_KEY)
    return stored === null ? true : stored === 'true'
  })
  const [saving, setSaving] = useState(false)
  const { notice: saveNotice, ok: noticeOk, fail: noticeFail, clear: clearNotice } = useNotice()
  const [redeemCode, setRedeemCode] = useState('')
  const [redeeming, setRedeeming] = useState(false)
  const { notice: redeemNotice, ok: redeemOk, fail: redeemFail, clear: clearRedeemNotice } = useNotice()

  // ── 桌面端设置 ──
  const [autoStart, setAutoStart] = useState(false)
  const [buildFactoryExpanded, setBuildFactoryExpanded] = useState(false)
  const [buildEnvInstalled, setBuildEnvInstalled] = useState(false)
  const [buildEnvVersion] = useState('')
  const [building, setBuilding] = useState(false)
  const [installingEnv, setInstallingEnv] = useState(false)
  const [lastBuild, setLastBuild] = useState('')
  const [downloadLink] = useState('')
  const [clearCacheMsg, setClearCacheMsg] = useState('')

  // 检测当前平台
  const getCurrentPlatform = (): 'windows' | 'macos' | 'linux' => {
    const ua = navigator.userAgent.toLowerCase()
    if (ua.includes('win')) return 'windows'
    if (ua.includes('mac')) return 'macos'
    return 'linux'
  }

  // ── 邮箱管理 ──
  const [showBindEmail, setShowBindEmail] = useState(false)
  const [bindEmail, setBindEmail] = useState('')
  const [bindCode, setBindCode] = useState('')
  const [bindCodeSent, setBindCodeSent] = useState(false)
  const [bindSendCooldown, setBindSendCooldown] = useState(0)
  const [bindError, setBindError] = useState('')
  const [bindLoading, setBindLoading] = useState(false)
  const [removeConfirm, setRemoveConfirm] = useState(false)
  const { rebindEmail, removeEmail } = useAuth()
  const bindCooldownRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 清理倒计时
  useEffect(() => {
    return () => { if (bindCooldownRef.current) clearInterval(bindCooldownRef.current) }
  }, [])

  const handleSendBindCode = async () => {
    if (!bindEmail || bindSendCooldown > 0) return
    try {
      await api.post('/auth/send-verification-code', { email: bindEmail, purpose: 'rebind' })
      setBindCodeSent(true)
      setBindError('')
      setBindSendCooldown(60)
      bindCooldownRef.current = setInterval(() => {
        setBindSendCooldown(prev => {
          if (prev <= 1) { if (bindCooldownRef.current) clearInterval(bindCooldownRef.current); return 0 }
          return prev - 1
        })
      }, 1000)
    } catch (err: any) {
      setBindError(err.message || t('auth:tooManyRequests'))
    }
  }

  const handleBind = async () => {
    if (!bindEmail || !bindCode) return
    setBindLoading(true)
    setBindError('')
    try {
      await rebindEmail(bindEmail, bindCode)
      await refreshUser()
      setShowBindEmail(false)
      setBindEmail(''); setBindCode(''); setBindCodeSent(false); setBindSendCooldown(0)
      if (bindCooldownRef.current) { clearInterval(bindCooldownRef.current); bindCooldownRef.current = null }
    } catch (err: any) {
      setBindError(err.message || t('auth:invalidCode'))
    } finally {
      setBindLoading(false)
    }
  }

  const handleRemoveEmail = async () => {
    try {
      await removeEmail()
      await refreshUser()
      setRemoveConfirm(false)
    } catch (err: any) {
      setBindError(err.message || t('error:saveFailed'))
    }
  }

  // ── 可拖拽侧边栏 ──
  const sidebarRef = useRef<HTMLDivElement>(null)
  const { sidebarWidth, handleResizeStart } = useResizableSidebar('settings_sidebar_width', sidebarRef)
  // 左侧分类导航的收起状态：与聊天列表、DSH、控制台共用一份存法
  const [collapsed, toggleRail] = useRailCollapsed('settings_sidebar_collapsed')

  // ── 滚动监听：高亮当前可见 section ──
  const contentRef = useRef<HTMLDivElement>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const initialTab = searchParams.get('tab') || 'api'
  const [activeTab, setActiveTab] = useState(initialTab)

  const switchTab = (tab: string) => {
    setActiveTab(tab)
    setSearchParams({ tab })
    setMobileView('detail')
  }
  const [mobileView, setMobileView] = useState<'list' | 'detail'>('list')

  useEffect(() => {
    const el = contentRef.current
    if (!el) return
    const ids = NAV_SECTIONS.map(s => `settings-${s.id}`)
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter(e => e.isIntersecting).sort((a, b) => {
          const aEl = a.target as HTMLElement
          const bEl = b.target as HTMLElement
          return aEl.offsetTop - bEl.offsetTop
        })
        if (visible.length > 0) {
          const id = visible[0].target.id.replace('settings-', '')
          setActiveTab(id)
        }
      },
      { root: el, rootMargin: '-60px 0px -60% 0px', threshold: 0 }
    )
    const targets: Element[] = []
    ids.forEach(id => {
      const t = document.getElementById(id)
      if (t) { observer.observe(t); targets.push(t) }
    })
    return () => targets.forEach(t => observer.unobserve(t))
  }, [])

  // ── 未保存修改检测 ──
  const [savedValues, setSavedValues] = useState<{
    autoTimeout: number; autoDefault: boolean
    timezone: string; language: string; chatStyle: string; uiScale: number
    preferOwnKey: boolean
  } | null>(null)

  const hasUnsavedChanges = savedValues !== null && (
    autoTimeout !== savedValues.autoTimeout ||
    autoDefault !== savedValues.autoDefault ||
    timezone !== savedValues.timezone ||
    language !== savedValues.language ||
    chatStyle !== savedValues.chatStyle ||
    uiScale !== savedValues.uiScale ||
    preferOwnKey !== savedValues.preferOwnKey
  )

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      hasUnsavedChanges && currentLocation.pathname !== nextLocation.pathname
  )

  useEffect(() => {
    if (!hasUnsavedChanges) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [hasUnsavedChanges])

  const handleNotificationToggle = (enabled: boolean) => {
    setNotifications(enabled)
    localStorage.setItem('notifications_enabled', enabled ? 'true' : 'false')
  }

  const handlePopupNotificationToggle = (enabled: boolean) => {
    setPopupNotifications(enabled)
    localStorage.setItem(IN_APP_NOTIFICATION_KEY, enabled ? 'true' : 'false')
  }

  useEffect(() => {
    if (user) {
      api.get('/auth/me').then((data) => {
        const tz = data.timezone || 'Asia/Shanghai'
        const lang = data.language || 'zh'
        const style = data.ui_prefs?.chat_style || 'cozy'
        const to = data.auto_approve_vector_timeout ?? 60
        const ad = data.auto_approve_vector_default ?? false
        setAutoTimeout(to)
        setAutoDefault(ad)
        setPreferOwnKey(data.prefer_own_key ?? false)
        if (data.timezone) setTimezone(tz)
        if (data.language) setLanguage(lang)
        if (data.ui_prefs?.chat_style) { setChatStyle(style); try { localStorage.setItem('chat_style', style) } catch {} }
        if (data.ui_prefs?.ui_scale) {
          const s = parseFloat(data.ui_prefs.ui_scale)
          if (s >= 0.7 && s <= 1.5) { setUiScale(s); try { localStorage.setItem('ui_scale', String(s)) } catch {} }
        }
        setSavedValues({
          preferOwnKey: data.prefer_own_key ?? false,
          autoTimeout: to,
          autoDefault: ad,
          timezone: tz,
          language: lang,
          chatStyle: style,
          uiScale: data.ui_prefs?.ui_scale || 1,
        })
      }).catch(console.error)
    }
  }, [user])

  // Hash 导航
  const location = useLocation()
  useEffect(() => {
    const hash = location.hash?.replace('#', '')
    if (hash) {
      const timer = setTimeout(() => {
        document.getElementById(`settings-${hash}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 200)
      return () => clearTimeout(timer)
    }
  }, [location.hash])

  const handleSave = async () => {
    setSaving(true)
    clearNotice()
    try {
      await api.put('/user/settings', {
        prefer_own_key: preferOwnKey,
        auto_approve_vector_timeout: autoTimeout,
        auto_approve_vector_default: autoDefault,
        timezone,
        language,
        ui_prefs: { chat_style: chatStyle, ui_scale: uiScale },
      })
      try { localStorage.setItem('chat_style', chatStyle); localStorage.setItem('ui_scale', String(uiScale)) } catch {}
      noticeOk(t('settings:saveSuccess'))
      setSavedValues({
        autoTimeout,
        autoDefault,
        timezone,
        language,
        chatStyle,
        uiScale,
        preferOwnKey,
      })
      saveToStorage(magicVision)
      refreshUser()
      apply(magicVision)
    } catch (err: any) {
      noticeFail(err.message || t('error:saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const handleRedeem = async () => {
    if (!redeemCode.trim()) return
    setRedeeming(true)
    clearRedeemNotice()
    try {
      const data = await api.post<{ message: string }>('/user/redeem', { code: redeemCode.trim() })
      redeemOk(data.message || t('common:redeemSuccess'))
      setRedeemCode('')
      refreshUser()
    } catch (err: any) {
      redeemFail(err.message || t('common:redeemFailed'))
    } finally {
      setRedeeming(false)
      setTimeout(clearRedeemNotice, 4000)
    }
  }

  /** 点击侧边栏 → 滚动到对应 section */
  const renderContent = () => {
    return sections;
  }

  // ── 所有 section 内容 ──
  const sections = (
    <div className="space-y-4">
      {/* 额度 */}
 <div id="settings-quota" className={(activeTab === 'quota' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Zap size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:quotaTitle')}</h2>
        </div>
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="bg-canvas rounded-card p-4 border border-border">
            <p className="text-xs text-textMuted mb-1">{t('settings:aiQuota')}</p>
            <p className="text-2xl font-bold text-textPrimary">{user?.ai_quota ?? 0}</p>
            <p className="text-3xs text-textMuted mt-1">{t('settings:aiQuotaHint')}</p>
          </div>
          <div className="bg-canvas rounded-card p-4 border border-border">
            <p className="text-xs text-textMuted mb-1">{t('settings:apiCredit')}</p>
            <p className="text-2xl font-bold text-textPrimary">{user?.api_credit ?? 0}</p>
            <p className="text-3xs text-textMuted mt-1">{t('settings:apiCreditHint')}</p>
          </div>
          <div className="bg-canvas rounded-card p-4 border border-border">
            <p className="text-xs text-textMuted mb-1">{t('settings:platformCredit')}</p>
            <p className="text-2xl font-bold text-textPrimary">{user?.platform_gifted_credit ?? 0}</p>
            <p className="text-3xs text-textMuted mt-1">{t('settings:platformCreditHint')}</p>
          </div>
          <div className="bg-canvas rounded-card p-4 border border-border">
            <p className="text-xs text-textMuted mb-1">{t('settings:fileQuota')}</p>
            <p className="text-2xl font-bold text-textPrimary">{user?.file_quota_mb ?? 0}<span className="text-sm font-normal text-textMuted"> MB</span></p>
            <p className="text-3xs text-textMuted mt-1">{t('settings:fileQuotaHint')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex-1 relative">
            <Ticket size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-textMuted" />
            <input
              type="text"
              value={redeemCode}
              onChange={(e) => setRedeemCode(e.target.value)}
              className="field field-with-icon w-full"
              placeholder={t('me:redeemPlaceholder')}
            />
          </div>
          <button
            onClick={handleRedeem}
            disabled={redeeming || !redeemCode.trim()}
            className="btn btn-sm btn-primary gap-1 shrink-0"
          >
            {redeeming ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            {t('me:redeem')}
          </button>
        </div>
        {redeemNotice && (
          <p className={`text-xs mt-2 ${NOTICE_TEXT_CLASS[redeemNotice.tone]}`}>
            {redeemNotice.text}
          </p>
        )}
      </div>

      {/* 邮箱管理 */}
 <div id="settings-email" className={(activeTab === 'email' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Mail size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('auth:email')}</h2>
        </div>

        {user?.email ? (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <span className="text-sm text-textPrimary truncate">{user.email}</span>
              {user.email_verified ? (
                <span className="chip chip-mint shrink-0">{t('auth:emailVerified')}</span>
              ) : (
                <span className="chip chip-accent shrink-0">{t('auth:emailNotVerified')}</span>
              )}
            </div>
            <p className="text-xs text-textMuted">{t('auth:emailVerified') ? t('settings:emailVerifiedDesc') : t('settings:emailNotVerifiedDesc')}</p>
            <div className="flex gap-2">
              <button
                onClick={() => { setShowBindEmail(true); setBindEmail(user.email || ''); setBindCode(''); setBindCodeSent(false); setBindError('') }}
                className="btn btn-sm btn-secondary"
              >
                {t('auth:changeEmail')}
              </button>
              {user.email && (
                <button
                  onClick={() => setRemoveConfirm(true)}
                  className="btn btn-sm btn-tinted-danger"
                >
                  {t('auth:removeEmail')}
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-textMuted">{t('settings:emailNotSetDesc')}</p>
            <button
              onClick={() => { setShowBindEmail(true); setBindEmail(''); setBindCode(''); setBindCodeSent(false); setBindError('') }}
              className="btn btn-sm btn-primary"
            >
              {t('auth:bindEmailTitle')}
            </button>
          </div>
        )}
      </div>

      {/* API 配置：厂商与配置档（LLM / 语音共用同一套结构） */}
 <div id="settings-api" className={(activeTab === 'api' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <Key size={18} className="text-primary-400 shrink-0" />
          <h2 className="font-semibold text-textPrimary whitespace-nowrap shrink-0">{t('settings:apiConfigTitle')}</h2>
          <span className="text-3xs text-textMuted ml-auto">{t('settings:apiConfigHint')}</span>
        </div>

        <div className="space-y-4">
          {/* LLM / 语音两块同构：厂商 + 配置档，每个 AI 在「API」页签里各挑一条 */}
          <ApiConfigSection kind="llm" />
          <ApiConfigSection kind="voice" />

          {/* 优先使用本人 API Key 开关 */}
          <div className="flex items-center justify-between py-3 border-t border-border mt-3">
            <div className="flex-1">
              <span className="text-sm font-medium text-textPrimary">{t('settings:preferOwnKey')}</span>
              <p className="text-xs text-textMuted mt-0.5">{t('settings:preferOwnKeyDesc')}</p>
            </div>
            <Toggle
              checked={preferOwnKey}
              onChange={setPreferOwnKey}
            />
          </div>
        </div>

      </div>

      {/* 时区设置 */}
 <div id="settings-timezone" className={(activeTab === 'timezone' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Clock size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:timezone')}</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('settings:timezoneDesc')}</p>
        <select
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          className="field w-full"
        >
          {TIMEZONES.map((tz) => (
            <option key={tz} value={tz}>{tz}</option>
          ))}
        </select>
        <p className="text-xs text-textMuted mt-2">
          {t('settings:currentTimestamp')} {fmtDateTime(new Date(), lang, { timeZone: timezone })}
        </p>
      </div>

      {/* 语言设置 */}
 <div id="settings-language" className={(activeTab === 'language' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Globe size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:language')}</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('settings:languageDesc')}</p>
        <select
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          className="field w-full"
        >
          {LANGUAGES.map((l) => (
            <option key={l.code} value={l.code}>{t(l.i18nKey)}</option>
          ))}
        </select>
      </div>

      {/* 聊天样式 */}
 <div id="settings-chatstyle" className={(activeTab === 'chatstyle' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Layout size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:chatStyle')}</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('settings:chatStyleDesc')}</p>
        <div className="flex gap-3">
          {CHAT_STYLES.map((s) => (
            // ui-standard-ignore: 聊天样式卡片（整卡可选），不是按钮形态
            <button
              key={s.value}
              onClick={() => setChatStyle(s.value)}
              className={`flex-1 px-4 py-3 rounded-card border text-sm font-medium transition-all text-left ${
                chatStyle === s.value
                  ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                  : 'border-border bg-canvas text-textSecondary hover:border-primary-500/30'
              }`}
            >
              <div className="font-medium">{t(s.key)}</div>
              <div className="text-3xs mt-0.5 opacity-70">{t(s.descKey)}</div>
            </button>
          ))}
        </div>
      </div>

      {/* UI 缩放 */}
 <div className={(activeTab === 'uiscale' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Layout size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:uiScale')}</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('settings:uiScaleDesc')}</p>
        <div className="flex gap-3">
          {[
            { value: 0.85, labelKey: 'settings:scaleSmall', descKey: 'settings:scaleSmallDesc' },
            { value: 1.0, labelKey: 'settings:scaleMedium', descKey: 'settings:scaleMediumDesc' },
            { value: 1.15, labelKey: 'settings:scaleLarge', descKey: 'settings:scaleLargeDesc' },
            { value: 1.3, labelKey: 'settings:scaleXLarge', descKey: 'settings:scaleXLargeDesc' },
          ].map((s) => (
            // ui-standard-ignore: 界面缩放卡片（整卡可选），不是按钮形态
            <button
              key={s.value}
              onClick={() => setUiScale(s.value)}
              className={`flex-1 px-4 py-3 rounded-card border text-sm font-medium transition-all text-left ${
                uiScale === s.value
                  ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                  : 'border-border bg-canvas text-textSecondary hover:border-primary-500/30'
              }`}
            >
              <div className="font-medium">{t(s.labelKey)}</div>
              <div className="text-3xs mt-0.5 opacity-70">{t(s.descKey)}</div>
            </button>
          ))}
        </div>
      </div>

      {/* 策略模式 */}
 <div id="settings-strategy" className={(activeTab === 'strategy' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Zap size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:strategy')}</h2>
        </div>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1.5 text-textSecondary">
              {t('settings:autoApproveTimeout')} {autoTimeout}
            </label>
            <Slider min={10} max={300} step={10} value={autoTimeout} onChange={setAutoTimeout} />
          </div>
          <div className="flex items-center gap-3">
            <Toggle checked={autoDefault} onChange={setAutoDefault} />
            <span className="text-sm text-textSecondary">{t('settings:autoApproveDefault')}</span>
          </div>
        </div>
      </div>

      {/* Mermaid 图表 */}
 <div id="settings-mermaid" className={(activeTab === 'mermaid' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:mermaidTitle')}</h2>
        </div>
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Toggle
              checked={getMermaidSetting('mermaid_collapse', true)}
              onChange={(v) => { try { localStorage.setItem('mermaid_collapse', String(v)); window.location.reload() } catch {} }}
            />
            <span className="text-sm text-textSecondary">{t('settings:mermaidCollapse')}</span>
          </div>
          <div className="flex items-center gap-3">
            <Toggle
              checked={getMermaidSetting('mermaid_collapse_errors', true)}
              onChange={(v) => { try { localStorage.setItem('mermaid_collapse_errors', String(v)); window.location.reload() } catch {} }}
            />
            <span className="text-sm text-textSecondary">{t('settings:mermaidCollapseErrors')}</span>
          </div>
        </div>
      </div>

      {/* 外观主题 */}
 <div id="settings-appearance" className={(activeTab === 'appearance' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Palette size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:appearance')}</h2>
          <span className="text-3xs px-2 py-0.5 rounded-full bg-mint-400/10 text-mint-400 border border-mint-400/20">{t('settings:instantApplyBadge')}</span>
        </div>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-textPrimary">{t('settings:darkMode')}</p>
            <p className="text-xs text-textMuted mt-0.5">
              {theme === 'dark' ? t('settings:darkModeDesc') : t('settings:lightModeDesc')}
            </p>
          </div>
          <Toggle checked={theme === 'dark'} onChange={() => toggleTheme()} />
        </div>

        {/* 主题定制 - 个性化主色 */}
        <ThemeCustomizer />

        {/* 皮肤插件 - 一键启用/停用 */}
        <SkinPicker />

        {/* 魔视界 - CSS 滤镜 */}
        <MagicVisionFilter
          value={magicVision}
          onChange={(v) => setMagicVision(v)}
        />
      </div>

      {/* 桌面通知 */}
 <div id="settings-notifications" className={(activeTab === 'notifications' ? '' : 'hidden') + ' card p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Bell size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings:notifications')}</h2>
          <span className="text-3xs px-2 py-0.5 rounded-full bg-mint-400/10 text-mint-400 border border-mint-400/20">{t('settings:instantApplyBadge')}</span>
        </div>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-textPrimary">{t('settings:notificationsLabel')}</p>
            <p className="text-xs text-textMuted mt-0.5">{t('settings:notificationsDetailDesc')}</p>
          </div>
          <Toggle checked={notifications} onChange={() => handleNotificationToggle(!notifications)} />
        </div>

        <div className="flex items-center justify-between mt-4 pt-4 border-t border-border/50">
          <div>
            <p className="text-sm font-medium text-textPrimary">{t('settings:notificationsPopupLabel')}</p>
            <p className="text-xs text-textMuted mt-0.5">{t('settings:notificationsPopupDesc')}</p>
          </div>
          <Toggle checked={popupNotifications} onChange={() => handlePopupNotificationToggle(!popupNotifications)} />
        </div>
      </div>

      {/* ── 桌面端设置 ── */}
      {isDesktop() && (
 <div id="settings-desktop" className="card p-3 md:p-6 scroll-mt-16">
          <div className="flex items-center gap-2 mb-4">
            <Monitor size={18} className="text-primary-400" />
            <h2 className="font-semibold text-textPrimary">{t('settings:desktopSection')}</h2>
            <span className="text-3xs px-2 py-0.5 rounded-full bg-accent-500/10 text-accent-500 border border-accent-500/20">{t('settings:desktopSectionDesc')}</span>
          </div>

          <div className="space-y-4">
            {/* 开机自启动 */}
            <div className="flex items-center justify-between p-3 rounded-card border border-border bg-canvas/50">
              <div>
                <p className="text-sm font-medium text-textPrimary">{t('settings:autoStart')}</p>
                <p className="text-xs text-textMuted mt-0.5">{t('settings:autoStartDesc')}</p>
              </div>
              <Toggle checked={autoStart} onChange={async (enabled) => {
                setAutoStart(enabled)
                try { await invoke('set_auto_start', { enabled }) }
                catch { /* Rust 端未实现时静默忽略 */ }
              }} />
            </div>

            {/* 数据存储位置 */}
            <div className="p-3 rounded-card border border-border bg-canvas/50">
              <div className="flex items-center gap-2 mb-1">
                <HardDrive size={14} className="text-textMuted" />
                <p className="text-sm font-medium text-textPrimary">{t('settings:dataLocation')}</p>
              </div>
              <p className="text-xs text-textMuted">{t('settings:dataLocationDesc')}</p>
              <p className="text-xs text-primary-400 font-mono mt-1.5">
                ~/.copree/data/
              </p>
            </div>

            {/* 清理缓存 */}
            <div className="flex items-center justify-between p-3 rounded-card border border-border bg-canvas/50">
              <div>
                <p className="text-sm font-medium text-textPrimary">{t('settings:clearCache')}</p>
                <p className="text-xs text-textMuted mt-0.5">{t('settings:clearCacheDesc')}</p>
              </div>
              <button
                onClick={async () => {
                  try {
                    await invoke('clear_cache')
                    setClearCacheMsg(t('settings:clearCacheSuccess'))
                  } catch { /* Rust 端未实现时静默忽略 */ }
                  setTimeout(() => setClearCacheMsg(''), 3000)
                }}
                className="btn btn-sm btn-secondary gap-1.5"
              >
                <Trash2 size={13} />
                {t('settings:clearCacheBtn')}
              </button>
            </div>
            {clearCacheMsg && (
              <p className="text-xs text-mint-400">{clearCacheMsg}</p>
            )}

            {/* 本地模型管理入口 */}
            {/* ui-standard-ignore: 入口行（图标 + 说明 + 箭头），不是按钮形态 */}
            <button
              onClick={() => navigate('/local-models')}
              className="w-full flex items-center justify-between p-3 rounded-card border border-border bg-canvas/50 hover:bg-elevated transition-colors text-left"
            >
              <div>
                <div className="flex items-center gap-2">
                  <Cpu size={14} className="text-textMuted" />
                  <p className="text-sm font-medium text-textPrimary">{t('settings:localModelEntry')}</p>
                </div>
                <p className="text-xs text-textMuted mt-0.5 ml-6">{t('settings:localModelEntryDesc')}</p>
              </div>
              <ExternalLink size={14} className="text-textMuted shrink-0" />
            </button>

            {/* 实例连接配置入口 */}
            {/* ui-standard-ignore: 入口行（图标 + 说明 + 箭头），不是按钮形态 */}
            <button
              onClick={() => navigate('/instance-setup')}
              className="w-full flex items-center justify-between p-3 rounded-card border border-border bg-canvas/50 hover:bg-elevated transition-colors text-left"
            >
              <div>
                <div className="flex items-center gap-2">
                  <Globe size={14} className="text-textMuted" />
                  <p className="text-sm font-medium text-textPrimary">{t('settings:instanceSetupEntry')}</p>
                </div>
                <p className="text-xs text-textMuted mt-0.5 ml-6">{t('settings:instanceSetupEntryDesc')}</p>
              </div>
              <ExternalLink size={14} className="text-textMuted shrink-0" />
            </button>

            {/* 桌面端构建工厂 */}
            <div className="p-3 rounded-card border border-border bg-canvas/50">
              <button
                onClick={() => setBuildFactoryExpanded(!buildFactoryExpanded)}
                className="w-full flex items-center justify-between text-left"
              >
                <div className="flex items-center gap-2">
                  {buildFactoryExpanded ? <ChevronDown size={14} className="text-textMuted" /> : <ChevronRight size={14} className="text-textMuted" />}
                  <Box size={14} className="text-textMuted" />
                  <p className="text-sm font-medium text-textPrimary">{t('settings:buildFactory')}</p>
                  <span className={`text-3xs px-1.5 py-0.5 rounded-full ${
                    buildEnvInstalled
                      ? 'bg-mint-400/10 text-mint-400 border border-mint-400/20'
                      : 'bg-border/30 text-textMuted'
                  }`}>
                    {buildEnvInstalled ? t('settings:buildFactoryInstalled') : t('settings:buildFactoryNotInstalled')}
                  </span>
                </div>
              </button>

              {buildFactoryExpanded && (
                <div className="mt-3 space-y-3">
                  <p className="text-xs text-textMuted">{t('settings:buildFactoryDesc')}</p>

                  {!buildEnvInstalled ? (
                    <div className="bg-canvas rounded-control p-4 border border-border">
                      <div className="flex items-center gap-2 mb-3">
                        <XCircle size={14} className="text-textMuted" />
                        <span className="text-sm text-textPrimary">
                          {t('settings:buildFactoryNotInstalled')}
                        </span>
                      </div>
                      <p className="text-xs text-textMuted mb-3">{t('settings:buildFactoryRequired')}</p>
                      <div className="flex gap-2">
                        <button
                          onClick={async () => {
                            setInstallingEnv(true)
                            try {
                              await invoke('install_build_env')
                              setBuildEnvInstalled(true)
                            } catch { /* Rust 端未实现时静默忽略 */ }
                            setInstallingEnv(false)
                          }}
                          disabled={installingEnv}
                          className="btn btn-sm btn-primary gap-1.5"
                        >
                          {installingEnv ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
                          {installingEnv ? t('settings:buildFactoryInstalling') : t('settings:buildFactoryInstall')}
                        </button>
                        <button className="btn btn-md btn-outline">
                          {t('settings:buildFactoryViewDocs')}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="bg-canvas rounded-control p-4 border border-border space-y-3">
                      <div className="flex items-center gap-2">
                        <CheckCircle size={14} className="text-mint-400" />
                        <span className="text-sm text-textPrimary">
                          {t('settings:buildFactoryInstalled')}
                        </span>
                        {buildEnvVersion && (
                          <span className="text-xs text-textMuted">{buildEnvVersion}</span>
                        )}
                      </div>

                      {/* 构建目标 */}
                      <div>
                        <p className="text-xs font-medium text-textSecondary mb-2">{t('settings:buildFactoryBuildTarget')}</p>
                        <div className="flex flex-wrap gap-2">
                          {[
                            { label: 'Windows (.exe)', plat: 'windows' as const },
                            { label: 'macOS (.dmg)', plat: 'macos' as const },
                            { label: 'Linux (.AppImage)', plat: 'linux' as const },
                          ].map(({ label, plat }) => {
                            const current = getCurrentPlatform()
                            const available = current === plat
                            return (
                              <button
                                key={plat}
                                disabled={!available}
                                title={available ? '' : t('settings:buildFactoryCrossPlatformHint')}
                                className="btn btn-sm btn-primary"
                              >
                                {label}
                              </button>
                            )
                          })}
                        </div>
                      </div>

                      <div className="flex gap-2">
                        <button
                          onClick={async () => {
                            setBuilding(true)
                            try {
                              await invoke('build_desktop', { target: getCurrentPlatform() })
                              setLastBuild(new Date().toLocaleString())
                            } catch { /* Rust 端未实现时静默忽略 */ }
                            setBuilding(false)
                          }}
                          disabled={building}
                          className="btn btn-sm btn-success gap-1.5"
                        >
                          {building ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
                          {building ? t('settings:buildFactoryBuilding') : t('settings:buildFactoryBuild')}
                        </button>
                        <button className="btn btn-md btn-outline">
                          {t('settings:buildFactoryViewLog')}
                        </button>
                      </div>

                      {lastBuild && (
                        <div className="text-xs text-textMuted">
                          {t('settings:buildFactoryLastBuild')}：{lastBuild}
                        </div>
                      )}

                      {downloadLink && (
                        <div className="text-xs">
                          <span className="text-textMuted">{t('settings:buildFactoryDownloadLink')}：</span>
                          <span className="text-primary-400 font-mono">{downloadLink}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )

  // 手机端底部是悬浮胶囊导航：sticky 贴 scrollport 底边会被它压住，所以上移一个导航高度
  // （--mobile-nav-reserved 是唯一出处，见 index.css）；桌面端没有导航，照旧贴底。
  const renderSaveFooter = () => (
    <div className="sticky bottom-[var(--mobile-nav-reserved)] md:bottom-0 z-10 bg-gradient-to-t from-canvas via-canvas/95 to-transparent pt-6 pb-2 px-3 md:px-6">
      {saveNotice && (
        <div className={`text-sm px-3 py-2 rounded-card mb-3 ${NOTICE_BOX_CLASS[saveNotice.tone]}`}>
          {saveNotice.text}
        </div>
      )}
      <button
        onClick={handleSave}
        disabled={saving}
        className="btn btn-md btn-primary gap-2"
      >
        <Save size={16} />
        {saving ? t('settings:saving') : t('settings:save')}
      </button>
      <p className="text-xs text-textMuted mt-2">{t('settings:saveHint')}</p>
    </div>
  )

  return (
    <div className="h-full flex flex-col bg-canvas">
      {/* 头部 */}
      <div className="top-bar px-4 h-14 flex items-center gap-2 shrink-0">
        {mobileView === 'detail' ? (
          <button
            onClick={() => setMobileView('list')}
            className="icon-btn-sm md:hidden -ml-1 text-textSecondary"
          >
            <ArrowLeft size={18} />
          </button>
        ) : (
          <button
            onClick={() => navigate('/me')}
            className="icon-btn-sm md:hidden -ml-1 text-textSecondary"
            title={t('nav:me')}
          >
            <ArrowLeft size={18} />
          </button>
        )}
        <h1 className="font-semibold text-textPrimary text-sm">{t('settings:title')}</h1>
      </div>

      {/* ── 移动端：分类导航列表 ── */}
      {mobileView === 'list' && (
        <div className="md:hidden flex-1 overflow-y-auto p-4 pb-nav bg-canvas space-y-4">
          {user?.role === 'admin' && (
            // ui-standard-ignore: 管理员入口横幅，不是按钮形态
            <button
              onClick={() => navigate('/admin')}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-card bg-primary-500/10 border border-primary-500/20 text-primary-400 hover:bg-primary-500/15 text-sm font-medium transition-colors"
            >
              <Shield size={16} />
              {t('settings:adminButton')}
            </button>
          )}
          {[...new Set(NAV_SECTIONS.map(s => s.category))].map(cat => {
            const items = NAV_SECTIONS.filter(s => s.category === cat)
            return (
              <div key={cat}>
                <div className="text-xs font-semibold text-textMuted uppercase tracking-wider px-1 mb-1.5">{t(cat)}</div>
                <div className="space-y-1">
                  {items.map(({ id, icon: Icon, labelKey }) => (
                    // ui-standard-ignore: 设置分类导航行，不是按钮形态
                    <button
                      key={id}
                      onClick={() => switchTab(id)}
                      className="w-full flex items-center gap-3 px-4 py-3 rounded-card hover:bg-elevated active:bg-border/50 transition-colors text-left"
                    >
                      <div className="w-9 h-9 rounded-control bg-primary-500/10 flex items-center justify-center shrink-0">
                        <Icon size={18} className="text-primary-400" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-textPrimary">{t(labelKey)}</div>
                      </div>
                      <ChevronRight size={16} className="text-textMuted shrink-0" />
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── 主体：桌面端侧边栏 + 内容，移动端纯内容 ── */}
      <div className={`flex-1 flex min-h-0 ${mobileView === 'list' ? 'md:flex hidden' : ''}`}>
        {/* 桌面端侧边栏导航（可拖拽宽度） */}
        <div ref={sidebarRef} className="hidden md:flex shrink-0 relative border-r border-border bg-surface" style={{ width: collapsed ? RAIL_W : sidebarWidth }}>
          <div className="flex flex-col w-full min-w-0">
            {/* 收起 / 展开：和管理页左栏同一个位置（顶部右端）与同一对图标 */}
            <div className={'flex items-center shrink-0 border-b border-border mt-1 ' + (collapsed ? 'justify-center' : 'pl-3 pr-1.5')}>
              {!collapsed && (
                <span className="flex-1 min-w-0 truncate text-2xs font-medium text-textMuted uppercase tracking-wider">
                  {t('settings:title')}
                </span>
              )}
              <button
                onClick={toggleRail}
                className="icon-btn-sm text-textMuted hover:text-textPrimary shrink-0"
                title={collapsed ? t('common:expand') : t('common:collapse')}
                aria-label={collapsed ? t('common:expand') : t('common:collapse')}
              >
                {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
              </button>
            </div>
          <div className="overflow-y-auto py-1 w-full flex-1 min-h-0">
            {/* 管理员快捷入口 */}
            {user?.role === 'admin' && (
              // ui-standard-ignore: 管理员入口横幅，不是按钮形态
              <button
                onClick={() => navigate('/admin')}
                title={t('settings:adminButton')}
                className={'flex items-center gap-2 mb-1 rounded-control bg-primary-500/10 border border-primary-500/20 text-primary-400 hover:bg-primary-500/15 text-sm font-medium transition-colors ' + (collapsed ? 'mx-auto w-9 h-9 justify-center' : 'mx-2 px-2 py-1.5')}
                style={collapsed ? undefined : { width: 'calc(100% - 16px)' }}
              >
                <Shield size={14} />
                {!collapsed && t('settings:adminButton')}
              </button>
            )}

            <nav>
              {/* 按分类分组 */}
              {(() => {
                const categories = [...new Set(NAV_SECTIONS.map(s => s.category))]
                return categories.map(cat => {
                  const items = NAV_SECTIONS.filter(s => s.category === cat)
                  return (
                    <div key={cat}>
                      {!collapsed && (
                        <div className="px-3 h-7 border-b border-border font-medium text-2xs text-textMuted uppercase tracking-wider flex items-center shrink-0">
                          {t(cat)}
                        </div>
                      )}
                      <div className="py-0.5">
                        {items.map(({ id, icon: Icon, labelKey }) => (
                          // ui-standard-ignore: 设置分类导航行，不是按钮形态
                          <button
                            key={id}
                            onClick={() => switchTab(id)}
                            title={collapsed ? t(labelKey) : undefined}
                            className={`w-full flex items-center gap-2 text-sm transition-colors text-left ${
                              collapsed ? 'justify-center px-0 py-2' : 'px-3 py-1.5'
                            } ${
                              activeTab === id
                                ? 'bg-primary-500/10 text-primary-600 dark:text-primary-400 font-medium border-r-2 border-primary-400'
                                : 'text-textSecondary hover:text-textPrimary hover:bg-elevated border-r-2 border-transparent'
                            }`}
                          >
                            <Icon size={collapsed ? 16 : 14} className="shrink-0 opacity-70" />
                            {!collapsed && <span className="truncate">{t(labelKey)}</span>}
                          </button>
                        ))}
                      </div>
                    </div>
                  )
                })
              })()}
            </nav>
          </div>
          </div>

          {/* 拖拽手柄：收起后没有宽度可拖，整条收起来 */}
          {!collapsed && (
            <div
              className="absolute top-0 -right-1.5 w-1.5 h-full cursor-col-resize hover:bg-primary-400/30 active:bg-primary-400/50 transition-colors z-overlay content-width-handles"
              onMouseDown={handleResizeStart}
            />
          )}
        </div>

        {/* 内容区域 */}
        <div ref={contentRef} className="flex-1 overflow-y-auto p-4 md:p-6 pb-0 md:pb-0">
          <div className="max-w-2xl mx-auto pb-4">
            {/* 移动端管理员快捷入口 */}
            {user?.role === 'admin' && (
              // ui-standard-ignore: 管理员入口横幅，不是按钮形态
              <button
                onClick={() => navigate('/admin')}
                className="md:hidden w-full flex items-center justify-center gap-2 px-4 py-3 mb-4 rounded-card bg-primary-500/10 border border-primary-500/20 text-primary-400 hover:bg-primary-500/15 text-sm font-medium transition-colors"
              >
                <Shield size={16} />
                {t('settings:adminButton')}
              </button>
            )}

            {renderContent()}
            {renderSaveFooter()}
          </div>
        </div>
      </div>

      {/* ── 邮箱绑定弹窗 ── */}
      {showBindEmail && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-modal p-4">
          <div className="bg-surface border border-border rounded-dialog p-6 w-full max-w-sm shadow-2xl shadow-black/30">
            <h3 className="text-lg font-semibold text-textPrimary mb-4">{t('auth:bindEmailTitle')}</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-textSecondary mb-1">{t('auth:email')}</label>
                <input
                  type="email"
                  value={bindEmail}
                  onChange={e => setBindEmail(e.target.value)}
                  className="field w-full"
                  placeholder={t('auth:emailPlaceholder')}
                />
              </div>
              <button
                onClick={handleSendBindCode}
                disabled={!bindEmail || bindSendCooldown > 0}
                className="btn btn-sm btn-secondary w-full"
              >
                {bindSendCooldown > 0 ? `${t('auth:codeResendIn')} ${bindSendCooldown}s` : bindCodeSent ? t('auth:codeSent') : t('auth:sendCode')}
              </button>
              <div>
                <label className="block text-xs text-textSecondary mb-2 text-center">{t('auth:codePlaceholder')}</label>
                <VerificationCodeInput
                  value={bindCode}
                  onChange={setBindCode}
                  disabled={bindLoading}
                  error={bindError}
                />
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setShowBindEmail(false)}
                  className="btn btn-sm btn-secondary flex-1"
                >
                  {t('common:cancel')}
                </button>
                <button
                  onClick={handleBind}
                  disabled={!bindEmail || !bindCode || bindLoading}
                  className="btn btn-sm btn-primary flex-1"
                >
                  {bindLoading ? t('common:saving') : t('common:confirm')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── 邮箱解绑确认弹窗 ── */}
      {removeConfirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-modal p-4">
          <div className="bg-surface border border-border rounded-dialog p-6 w-full max-w-sm shadow-2xl shadow-black/30">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-full bg-rose-500/10 flex items-center justify-center shrink-0">
                <AlertTriangle size={20} className="text-rose-400" />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-lg font-semibold text-textPrimary">{t('settings:removeEmailTitle')}</h3>
                <p className="text-sm text-textSecondary mt-1">{t('settings:removeEmailDesc')}</p>
              </div>
            </div>
            <div className="flex gap-3 mt-5">
              <button
                onClick={() => setRemoveConfirm(false)}
                className="btn btn-sm btn-secondary flex-1"
              >
                {t('common:cancel')}
              </button>
              <button
                onClick={handleRemoveEmail}
                className="btn btn-sm btn-danger flex-1"
              >
                {t('common:confirm')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── 未保存修改离开确认弹窗 ── */}
      {blocker.state === 'blocked' && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-modal p-4">
          <div className="bg-surface border border-border rounded-dialog p-6 w-full max-w-sm shadow-2xl shadow-black/30">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-full bg-accent-500/10 flex items-center justify-center shrink-0">
                <AlertTriangle size={20} className="text-accent-500" />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-lg font-semibold text-textPrimary">{t('settings:unsavedTitle')}</h3>
                <p className="text-sm text-textSecondary mt-1">{t('settings:unsavedDesc')}</p>
              </div>
            </div>
            <div className="flex gap-3 mt-5">
              <button
                onClick={() => blocker.reset?.()}
                className="btn btn-sm btn-secondary flex-1"
              >
                {t('settings:continueEditing')}
              </button>
              <button
                onClick={() => blocker.proceed?.()}
                className="btn btn-sm btn-danger flex-1"
              >
                {t('settings:discardChanges')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}