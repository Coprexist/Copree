import { useState, useEffect, useRef } from 'react'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'
import { useT } from '../i18n/I18nContext'
import Toggle from '../components/Toggle'
import { IN_APP_NOTIFICATION_KEY } from '../hooks/useNotificationSocket'
import { EXPRESSION_MODES } from '../components/ExpressionModeSwitch'
import MagicVisionFilter from '../components/MagicVisionFilter.tsx'
import ThemeCustomizer from '../components/ThemeCustomizer'
import SkinPicker from '../components/SkinPicker'
import { normalizePrefs, defaultPrefs, loadFromStorage, saveToStorage, apply } from '../utils/cssFilters'
import type { MagicVisionPrefs } from '../utils/cssFilters'
import { useResizableSidebar } from '../hooks/useResizableSidebar'
import VerificationCodeInput from '../components/VerificationCodeInput'
import { LANGUAGES } from '../i18n/languages'
import { isDesktop } from '../utils/platform'
import { invoke } from '../utils/tauri'
import { getApiKeyUrl } from '../utils/providers.tsx'
import { Key, Zap, Save, Clock, Palette, Bell, Eye, EyeOff, CheckCircle, XCircle, Loader2, Globe, Layout, BookOpenText, Bot, Pencil, X, Ticket, Plus, ChevronDown, ChevronRight, Shield, AlertTriangle, ArrowLeft, Mail, Monitor, HardDrive, Trash2, Cpu, Wrench, Box, ExternalLink, BarChart3, Star } from 'lucide-react'
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
  { value: 'cozy', key: 'settings.cozyMode', descKey: 'settings.cozyModeDesc' },
  { value: 'compact', key: 'settings.compactMode', descKey: 'settings.compactModeDesc' },
]

/** 侧边栏导航项（含分类） */
interface NavSection {
  id: string
  icon: React.ElementType
  labelKey: string
  category: string
}
const NAV_SECTIONS: NavSection[] = [
  { id: 'quota',       icon: Zap,     labelKey: 'settings.quotaTitle',      category: 'settings.catAccount' },
  { id: 'email',       icon: Mail,    labelKey: 'auth.email',                category: 'settings.catAccount' },
  { id: 'api',         icon: Key,     labelKey: 'settings.apiConfigTitle',   category: 'settings.catApi' },
  { id: 'timezone',    icon: Clock,   labelKey: 'settings.timezone',         category: 'settings.catPrefs' },
  { id: 'language',    icon: Globe,   labelKey: 'settings.language',         category: 'settings.catPrefs' },
  { id: 'chatstyle',   icon: Layout,   labelKey: 'settings.chatStyle',       category: 'settings.catPrefs' },
  { id: 'expressionmode', icon: BookOpenText, labelKey: 'settings.expressionMode', category: 'settings.catPrefs' },
  { id: 'uiscale',    icon: Monitor,  labelKey: 'UI 缩放',                  category: 'settings.catPrefs' },
  { id: 'mermaid',    icon: BarChart3, labelKey: 'Mermaid 图表',             category: 'settings.catPrefs' },
  { id: 'appearance',  icon: Palette, labelKey: 'settings.appearance',       category: 'settings.catPrefs' },
  { id: 'notifications', icon: Bell,  labelKey: 'settings.notifications',   category: 'settings.catPrefs' },
  { id: 'strategy',    icon: Zap,     labelKey: 'settings.strategy',         category: 'settings.catPrefs' },
  ...(isDesktop() ? [
    { id: 'desktop',  icon: Monitor, labelKey: 'settings.desktopSection',     category: 'settings.catDesktop' },
  ] : []),
]

export default function SettingsPage() {
  const t = useT()
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
  const [apiBaseUrl, setApiBaseUrl] = useState('https://api.deepseek.com')
  const [apiKey, setApiKey] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<'success' | 'fail' | null>(null)
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
  const [plainLanguage, setPlainLanguage] = useState(false)

  // 新增：全局默认模型（用户覆盖）
  const [globalChatModel, setGlobalChatModel] = useState('')
  const [globalWorkModel, setGlobalWorkModel] = useState('')

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
  const [message, setMessage] = useState('')
  const [agents, setAgents] = useState<any[]>([])
  const [editingAgentId, setEditingAgentId] = useState<number | null>(null)
  const [agentApiBaseUrl, setAgentApiBaseUrl] = useState('')
  const [agentApiKey, setAgentApiKey] = useState('')
  const [agentApiSaving, setAgentApiSaving] = useState(false)
  const [redeemCode, setRedeemCode] = useState('')
  const [redeeming, setRedeeming] = useState(false)
  const [redeemMsg, setRedeemMsg] = useState('')
  const [showAgentApi, setShowAgentApi] = useState(false)

  // ── 预设供应商列表 ──
  interface ProviderPreset { name: string; provider: string; base_url: string; api_key_url: string; chat_model: string; work_model: string; is_default: boolean; models: { value: string; label: string }[] }
  const [providerPresets, setProviderPresets] = useState<ProviderPreset[]>([])
  // 当前 base_url 对应的预设 + 模型下拉候选（预设模型列表 ∪ 当前值，去重）
  const curPreset = providerPresets.find(p => p.base_url === apiBaseUrl)
  const presetModelValues = (curPreset?.models || []).map(m => m.value)
  const allChatOptions = [...new Set([curPreset?.chat_model, ...presetModelValues, globalChatModel].filter(Boolean))]
  const allWorkOptions = [...new Set([curPreset?.work_model, ...presetModelValues, globalWorkModel].filter(Boolean))]
  // 选预设：填 base_url + 自动填模型。仅当当前值为空、或值本身来自某个预设时才覆盖，
  // 用户手写的自定义模型不被冲掉。判定无状态 → 刷新页面后依然成立
  // （此前用 chatModelManual 记忆"是否手动改过"，刷新即丢失，自定义模型会被预设冲掉）
  const isPresetModel = (v: string, kind: 'chat' | 'work') =>
    !v || providerPresets.some(p => (kind === 'chat' ? p.chat_model : p.work_model) === v)
  const applyPreset = (p: ProviderPreset) => {
    setApiBaseUrl(p.base_url)
    if (p.chat_model && isPresetModel(globalChatModel, 'chat')) setGlobalChatModel(p.chat_model)
    if (p.work_model && isPresetModel(globalWorkModel, 'work')) setGlobalWorkModel(p.work_model)
  }

  // ── 桌面端设置 ──
  const [autoStart, setAutoStart] = useState(false)
  const [buildFactoryExpanded, setBuildFactoryExpanded] = useState(false)
  const [buildEnvInstalled, setBuildEnvInstalled] = useState(false)
  const [buildEnvVersion, setBuildEnvVersion] = useState('')
  const [building, setBuilding] = useState(false)
  const [installingEnv, setInstallingEnv] = useState(false)
  const [lastBuild, setLastBuild] = useState('')
  const [downloadLink, setDownloadLink] = useState('')
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
      setBindError(err.message || t('auth.tooManyRequests'))
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
      setBindError(err.message || t('auth.invalidCode'))
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
      setBindError(err.message || t('error.saveFailed'))
    }
  }

  // ── 可拖拽侧边栏 ──
  const sidebarRef = useRef<HTMLDivElement>(null)
  const { sidebarWidth, handleResizeStart } = useResizableSidebar('settings_sidebar_width', sidebarRef)

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
    apiBaseUrl: string; autoTimeout: number; autoDefault: boolean
    timezone: string; language: string; chatStyle: string; uiScale: number; plainLanguage: boolean
    globalChatModel?: string; globalWorkModel?: string; preferOwnKey?: boolean
  } | null>(null)

  const hasUnsavedChanges = savedValues !== null && (
    apiBaseUrl !== savedValues.apiBaseUrl ||
    autoTimeout !== savedValues.autoTimeout ||
    autoDefault !== savedValues.autoDefault ||
    timezone !== savedValues.timezone ||
    language !== savedValues.language ||
    chatStyle !== savedValues.chatStyle ||
    uiScale !== savedValues.uiScale ||
    plainLanguage !== savedValues.plainLanguage ||
    globalChatModel !== (savedValues.globalChatModel || '') ||
    globalWorkModel !== (savedValues.globalWorkModel || '') ||
    apiKey.trim() !== ''
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
        const apiUrl = data.api_base_url || 'https://api.deepseek.com'
        const tz = data.timezone || 'Asia/Shanghai'
        const lang = data.language || 'zh'
        const style = data.ui_prefs?.chat_style || 'cozy'
        const plain = data.ui_prefs?.plain_language === true
        const to = data.auto_approve_vector_timeout ?? 60
        const ad = data.auto_approve_vector_default ?? false
        setApiBaseUrl(apiUrl)
        setAutoTimeout(to)
        setAutoDefault(ad)
        setPreferOwnKey(data.prefer_own_key ?? false)
        if (data.timezone) setTimezone(tz)
        if (data.language) setLanguage(lang)
        if (data.ui_prefs?.chat_style) { setChatStyle(style); try { localStorage.setItem('chat_style', style) } catch {} }
        setPlainLanguage(plain)
        if (data.ui_prefs?.ui_scale) {
          const s = parseFloat(data.ui_prefs.ui_scale)
          if (s >= 0.7 && s <= 1.5) { setUiScale(s); try { localStorage.setItem('ui_scale', String(s)) } catch {} }
        }
        // 加载全局默认模型覆盖
        setGlobalChatModel(data.global_chat_model || '')
        setGlobalWorkModel(data.global_work_model || '')
        setSavedValues({
          preferOwnKey: data.prefer_own_key ?? false,
          apiBaseUrl: apiUrl,
          autoTimeout: to,
          autoDefault: ad,
          timezone: tz,
          language: lang,
          chatStyle: style,
          uiScale: data.ui_prefs?.ui_scale || 1,
          plainLanguage: plain,
          globalChatModel: data.global_chat_model || '',
          globalWorkModel: data.global_work_model || '',
        })
      }).catch(console.error)
      api.get<any[]>('/agents').then(list => {
        setAgents(list || [])
      }).catch(() => {})
      api.get<{providers: ProviderPreset[]}>('/agents/models').then(d => {
        setProviderPresets(d.providers || [])
      }).catch(() => {})
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

  const handleTestConnection = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const data = await api.post<{ ok: boolean; message: string }>('/user/test-api-connection', {
        api_base_url: apiBaseUrl || null,
        api_key: apiKey || null,
      })
      setTestResult(data.ok ? 'success' : 'fail')
    } catch (err: any) {
      setTestResult('fail')
    } finally {
      setTesting(false)
      setTimeout(() => setTestResult(null), 5000)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage('')
    try {
      await api.put('/user/settings', {
        prefer_own_key: preferOwnKey,
        api_base_url: apiBaseUrl || null,
        api_key: apiKey || null,
        auto_approve_vector_timeout: autoTimeout,
        auto_approve_vector_default: autoDefault,
        timezone,
        language,
        ui_prefs: { chat_style: chatStyle, ui_scale: uiScale, plain_language: plainLanguage },
        global_chat_model: globalChatModel || null,
        global_work_model: globalWorkModel || null,
      })
      try { localStorage.setItem('chat_style', chatStyle); localStorage.setItem('ui_scale', String(uiScale)) } catch {}
      setApiKey('')
      setMessage(t('settings.saveSuccess'))
      setSavedValues({
        apiBaseUrl: apiBaseUrl || '',
        autoTimeout,
        autoDefault,
        timezone,
        language,
        chatStyle,
        uiScale,
        plainLanguage,
        globalChatModel,
        globalWorkModel,
      })
      saveToStorage(magicVision)
      refreshUser()
      apply(magicVision)
      api.get<any[]>('/agents').then(list => setAgents(list || [])).catch(() => {})
    } catch (err: any) {
      setMessage(err.message || t('error.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const handleSaveAgentApi = async (agentId: number) => {
    setAgentApiSaving(true)
    try {
      await api.put(`/agents/${agentId}/config`, {
        api_base_url: agentApiBaseUrl || null,
        api_key: agentApiKey || null,
      })
      setEditingAgentId(null)
      setAgentApiKey('')
      api.get<any[]>('/agents').then(list => setAgents(list || [])).catch(() => {})
    } catch (err: any) {
      alert(err.message || t('error.saveFailed'))
    } finally {
      setAgentApiSaving(false)
    }
  }

  const startEditAgentApi = (agent: any) => {
    setEditingAgentId(agent.id)
    const saved = agent.api_base_url || ''
    setAgentApiBaseUrl(saved || (providerPresets.find(p => p.is_default)?.base_url || ''))
    setAgentApiKey('')
  }

  const handleRedeem = async () => {
    if (!redeemCode.trim()) return
    setRedeeming(true)
    setRedeemMsg('')
    try {
      const data = await api.post<{ message: string }>('/user/redeem', { code: redeemCode.trim() })
      setRedeemMsg(data.message || t('common.redeemSuccess'))
      setRedeemCode('')
      refreshUser()
    } catch (err: any) {
      setRedeemMsg(err.message || t('common.redeemFailed'))
    } finally {
      setRedeeming(false)
      setTimeout(() => setRedeemMsg(''), 4000)
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
      <div id="settings-quota" className={(activeTab === 'quota' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Zap size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings.quotaTitle')}</h2>
        </div>
        <div className="grid grid-cols-2 gap-3 mb-4">
          <div className="bg-canvas rounded-card p-4 border border-border">
            <p className="text-xs text-textMuted mb-1">{t('settings.aiQuota')}</p>
            <p className="text-2xl font-bold text-textPrimary">{user?.ai_quota ?? 0}</p>
            <p className="text-3xs text-textMuted mt-1">{t('settings.aiQuotaHint')}</p>
          </div>
          <div className="bg-canvas rounded-card p-4 border border-border">
            <p className="text-xs text-textMuted mb-1">{t('settings.apiCredit')}</p>
            <p className="text-2xl font-bold text-textPrimary">{user?.api_credit ?? 0}</p>
            <p className="text-3xs text-textMuted mt-1">{t('settings.apiCreditHint')}</p>
          </div>
          <div className="bg-canvas rounded-card p-4 border border-border">
            <p className="text-xs text-textMuted mb-1">{t('settings.platformCredit')}</p>
            <p className="text-2xl font-bold text-textPrimary">{user?.platform_gifted_credit ?? 0}</p>
            <p className="text-3xs text-textMuted mt-1">{t('settings.platformCreditHint')}</p>
          </div>
          <div className="bg-canvas rounded-card p-4 border border-border">
            <p className="text-xs text-textMuted mb-1">{t('settings.fileQuota')}</p>
            <p className="text-2xl font-bold text-textPrimary">{user?.file_quota_mb ?? 0}<span className="text-sm font-normal text-textMuted"> MB</span></p>
            <p className="text-3xs text-textMuted mt-1">{t('settings.fileQuotaHint')}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex-1 relative">
            <Ticket size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-textMuted" />
            <input
              type="text"
              value={redeemCode}
              onChange={(e) => setRedeemCode(e.target.value)}
              className="w-full pl-9 pr-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              placeholder={t('me.redeemPlaceholder')}
            />
          </div>
          <button
            onClick={handleRedeem}
            disabled={redeeming || !redeemCode.trim()}
            className="btn btn-sm btn-primary gap-1 shrink-0"
          >
            {redeeming ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            {t('me.redeem')}
          </button>
        </div>
        {redeemMsg && (
          <p className={`text-xs mt-2 ${redeemMsg.includes('失败') ? 'text-rose-400' : 'text-mint-400'}`}>
            {redeemMsg}
          </p>
        )}
      </div>

      {/* 邮箱管理 */}
      <div id="settings-email" className={(activeTab === 'email' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Mail size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('auth.email')}</h2>
        </div>

        {user?.email ? (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <span className="text-sm text-textPrimary truncate">{user.email}</span>
              {user.email_verified ? (
                <span className="chip chip-mint shrink-0">{t('auth.emailVerified')}</span>
              ) : (
                <span className="chip chip-accent shrink-0">{t('auth.emailNotVerified')}</span>
              )}
            </div>
            <p className="text-xs text-textMuted">{t('auth.emailVerified') ? t('settings.emailVerifiedDesc') : t('settings.emailNotVerifiedDesc')}</p>
            <div className="flex gap-2">
              <button
                onClick={() => { setShowBindEmail(true); setBindEmail(user.email || ''); setBindCode(''); setBindCodeSent(false); setBindError('') }}
                className="px-3 py-1.5 text-xs rounded-control border border-border hover:bg-elevated text-textSecondary transition-colors"
              >
                {t('auth.changeEmail')}
              </button>
              {user.email && (
                <button
                  onClick={() => setRemoveConfirm(true)}
                  className="px-3 py-1.5 text-xs rounded-control border border-rose-500/20 text-rose-400 hover:bg-rose-500/10 transition-colors"
                >
                  {t('auth.removeEmail')}
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-textMuted">{t('settings.emailNotSetDesc')}</p>
            <button
              onClick={() => { setShowBindEmail(true); setBindEmail(''); setBindCode(''); setBindCodeSent(false); setBindError('') }}
              className="px-3 py-1.5 text-xs rounded-control bg-primary-500 hover:bg-primary-600 text-white font-medium transition-colors"
            >
              {t('auth.bindEmailTitle')}
            </button>
          </div>
        )}
      </div>

      {/* API 提供商配置 */}
      <div id="settings-api" className={(activeTab === 'api' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <Key size={18} className="text-primary-400 shrink-0" />
          <h2 className="font-semibold text-textPrimary whitespace-nowrap shrink-0">{t('settings.apiConfigTitle')}</h2>
          <span className="text-3xs text-textMuted ml-auto">{t('settings.apiConfigHint')}</span>
        </div>

        <div className="space-y-4">
          {providerPresets.length > 0 && (
            <div>
              <label className="block text-xs font-medium mb-1.5 text-textSecondary">{t('settings.presetProvider')}</label>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 mb-2">
                {providerPresets.map(p => (
                  <button
                    key={p.name}
                    onClick={() => applyPreset(p)}
                    className={`px-3 py-1.5 text-xs rounded-control font-medium transition-colors border truncate ${
                      apiBaseUrl === p.base_url
                        ? 'bg-primary-500/15 border-primary-500/40 text-primary-500'
                        : 'bg-canvas border-border text-textSecondary hover:text-textPrimary hover:border-primary-500/30'
                    }`}
                  >
                    {p.name}{p.is_default ? <Star size={10} className="fill-current text-accent-400" /> : null}
                  </button>
                ))}
              </div>
              {curPreset && (
                <p className="text-3xs text-mint-400">{t('settings.currentPreset')}：{curPreset.name} · {curPreset.base_url}</p>
              )}
            </div>
          )}
          <div>
            <label className="block text-xs font-medium mb-1.5 text-textSecondary">{t('settings.baseUrl')}</label>
            <input
              type="text"
              value={apiBaseUrl}
              onChange={(e) => setApiBaseUrl(e.target.value)}
              className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
              placeholder={t('settings.baseUrlPlaceholder')}
            />
            <p className="text-3xs text-textMuted mt-1">{t('settings.baseUrlDesc')}</p>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1.5 text-textSecondary flex items-center gap-2">
              {t('settings.apiKey')}
              {apiBaseUrl && getApiKeyUrl(apiBaseUrl) && (
                <a
                  href={getApiKeyUrl(apiBaseUrl)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-3xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 underline underline-offset-2 font-normal"
                >
                  获取 API Key →
                </a>
              )}
            </label>
            <div className="relative">
              <input
                type={showApiKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="w-full px-3.5 py-2.5 pr-10 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                placeholder={user?.has_api_key ? t('settings.apiKeyPlaceholderSet') : t('settings.apiKeyPlaceholder')}
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-textMuted hover:text-textSecondary"
              >
                {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <div className="flex items-center justify-between mt-1.5">
              <p className="text-xs text-textMuted">
                {user?.has_api_key
                  ? <><CheckCircle size={12} className="inline text-mint-400 mr-1" />{t('settings.apiKeySet')}</>
                  : <><AlertTriangle size={12} className="inline text-accent-400 mr-1" />{t('settings.apiKeyNotSet')}</>}
              </p>
              <button
                onClick={handleTestConnection}
                disabled={testing}
                className="flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 disabled:opacity-50 transition-colors"
              >
                {testing ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : testResult === 'success' ? (
                  <CheckCircle size={12} className="text-mint-400" />
                ) : testResult === 'fail' ? (
                  <XCircle size={12} className="text-rose-400" />
                ) : null}
                {testing ? t('settings.testing') : testResult === 'success' ? t('settings.testSuccess') : testResult === 'fail' ? t('settings.testFailed') : t('settings.testConnection')}
              </button>
            </div>
          </div>

          {/* 全局默认模型覆盖 */}
          <div className="grid grid-cols-2 gap-3 pt-3 border-t border-border">
            <div>
              <label className="block text-xs font-medium mb-1.5 text-textSecondary">
                全局默认聊天模型（覆盖）
              </label>
              <input
                type="text"
                value={globalChatModel}
                onChange={(e) => setGlobalChatModel(e.target.value)}
                list="chat-model-options"
                className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                placeholder="例如：mimo-v2.5"
              />
              <datalist id="chat-model-options">
                {allChatOptions.map(m => <option key={m} value={m} />)}
              </datalist>
              <p className="text-3xs text-textMuted mt-1">留空则使用系统全局默认</p>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1.5 text-textSecondary">
                全局默认工作模型（覆盖）
              </label>
              <input
                type="text"
                value={globalWorkModel}
                onChange={(e) => setGlobalWorkModel(e.target.value)}
                list="work-model-options"
                className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                placeholder="例如：mimo-v2.5-pro"
              />
              <datalist id="work-model-options">
                {allWorkOptions.map(m => <option key={m} value={m} />)}
              </datalist>
              <p className="text-3xs text-textMuted mt-1">留空则使用系统全局默认</p>
            </div>
          </div>

          {/* 优先使用本人 API Key 开关 */}
          <div className="flex items-center justify-between py-3 border-t border-border mt-3">
            <div className="flex-1">
              <span className="text-sm font-medium text-textPrimary">优先使用本人 API Key</span>
              <p className="text-xs text-textMuted mt-0.5">开启后优先使用自己的 API Key，再尝试系统额度</p>
            </div>
            <Toggle
              checked={preferOwnKey}
              onChange={setPreferOwnKey}
            />
          </div>
        </div>

        {/* 单 AI API 配置 */}
        <div className="mt-4 pt-4 border-t border-border">
          <button
            onClick={() => setShowAgentApi(!showAgentApi)}
            className="flex items-center gap-2 w-full text-left hover:bg-canvas/50 rounded-control px-2 py-1.5 -mx-2 transition-colors"
          >
            {showAgentApi ? <ChevronDown size={16} className="text-textMuted" /> : <ChevronRight size={16} className="text-textMuted" />}
            <Bot size={16} className="text-primary-400" />
            <span className="text-sm font-medium text-textPrimary">{t('settings.perAgentTitle')}</span>
            <span className="text-3xs text-textMuted">{t('settings.perAgentHint')}</span>
          </button>

          {showAgentApi && (
            <div className="mt-3">
              {agents.length === 0 ? (
                <p className="text-xs text-textMuted py-4 text-center">{t('settings.noAgentsForApi')}</p>
              ) : (
                <div className="space-y-2">
                  {agents.map((agent: any) => (
                    <div key={agent.id} className="bg-canvas rounded-card border border-border p-3">
                      {editingAgentId === agent.id ? (
                        <div className="space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-sm font-medium text-textPrimary">{agent.name}</span>
                            <button onClick={() => setEditingAgentId(null)} className="text-textMuted hover:text-textSecondary">
                              <X size={14} />
                            </button>
                          </div>
                          {providerPresets.length > 0 && (
                            <>
                              <label className="block text-xs text-textSecondary mb-1">厂商预设</label>
                              <div className="flex flex-wrap gap-1.5 mb-1">
                                {providerPresets.map(p => (
                                  <button
                                    key={p.name}
                                    onClick={() => setAgentApiBaseUrl(p.base_url)}
                                    className={`px-2 py-1 text-2xs rounded-control font-medium transition-colors border ${
                                      agentApiBaseUrl === p.base_url
                                        ? 'bg-primary-500/15 border-primary-500/40 text-primary-500'
                                        : 'bg-surface border-border text-textMuted hover:text-textSecondary hover:border-primary-500/30'
                                    }`}
                                  >
                                    {p.name}
                                  </button>
                                ))}
                              </div>
                              {(() => { const cur = providerPresets.find(p => p.base_url === agentApiBaseUrl); return cur ? (
                                <p className="text-3xs text-mint-400">{t('settings.currentPreset')}：{cur.name}</p>
                              ) : null })()}
                            </>
                          )}
                          <input
                            type="text"
                            value={agentApiBaseUrl}
                            onChange={(e) => setAgentApiBaseUrl(e.target.value)}
                            className="w-full px-3 py-1.5 rounded-control border border-border bg-surface text-xs text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                            placeholder={t('settings.agentBaseUrlPlaceholder')}
                          />
                          <input
                            type="password"
                            value={agentApiKey}
                            onChange={(e) => setAgentApiKey(e.target.value)}
                            className="w-full px-3 py-1.5 rounded-control border border-border bg-surface text-xs text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-1 focus:ring-primary-500/50"
                            placeholder={t('settings.agentApiKeyPlaceholder')}
                          />
                          <button
                            onClick={() => handleSaveAgentApi(agent.id)}
                            disabled={agentApiSaving}
                            className="w-full py-1.5 rounded-control bg-primary-500 text-white text-xs font-medium hover:bg-primary-600 disabled:opacity-40 transition-colors"
                          >
                            {agentApiSaving ? t('settings.agentApiSaving') : t('common.save')}
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <span className="text-sm text-textPrimary">{agent.name}</span>
                            {agent.api_base_url ? (
                              <span className="text-3xs px-1.5 py-0.5 rounded bg-primary-500/10 text-primary-400">
                                {t('settings.independentApiBadge')}
                              </span>
                            ) : (
                              <span className="text-3xs px-1.5 py-0.5 rounded bg-border/30 text-textMuted">
                                {t('settings.inheritGlobalBadge')}
                              </span>
                            )}
                          </div>
                          <button
                            onClick={() => startEditAgentApi(agent)}
                            className="text-textMuted hover:text-textSecondary transition-colors"
                          >
                            <Pencil size={13} />
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 时区设置 */}
      <div id="settings-timezone" className={(activeTab === 'timezone' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Clock size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings.timezone')}</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('settings.timezoneDesc')}</p>
        <select
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
        >
          {TIMEZONES.map((tz) => (
            <option key={tz} value={tz}>{tz}</option>
          ))}
        </select>
        <p className="text-xs text-textMuted mt-2">
          {t('settings.currentTimestamp')} {new Date().toLocaleString('zh-CN', { timeZone: timezone })}
        </p>
      </div>

      {/* 语言设置 */}
      <div id="settings-language" className={(activeTab === 'language' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Globe size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings.language')}</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('settings.languageDesc')}</p>
        <select
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
        >
          {LANGUAGES.map((l) => (
            <option key={l.code} value={l.code}>{t(l.i18nKey)}</option>
          ))}
        </select>
      </div>

      {/* 聊天样式 */}
      <div id="settings-chatstyle" className={(activeTab === 'chatstyle' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Layout size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings.chatStyle')}</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('settings.chatStyleDesc')}</p>
        <div className="flex gap-3">
          {CHAT_STYLES.map((s) => (
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

      {/* 表达方式（专业模式 / 通俗模式）：两段直选，与聊天输入工具条的那对按钮同一套名字 */}
      <div id="settings-expressionmode" className={(activeTab === 'expressionmode' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <BookOpenText size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings.expressionMode')}</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">{t('settings.expressionModeDesc')}</p>
        <div className="flex gap-3">
          {EXPRESSION_MODES.map((m) => (
            <button
              key={String(m.plain)}
              onClick={() => setPlainLanguage(m.plain)}
              className={`flex-1 px-4 py-3 rounded-card border text-sm font-medium transition-all text-left ${
                plainLanguage === m.plain
                  ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                  : 'border-border bg-canvas text-textSecondary hover:border-primary-500/30'
              }`}
            >
              <div className="font-medium">{t(m.labelKey)}</div>
              <div className="text-3xs mt-0.5 opacity-70">{t(m.descKey)}</div>
            </button>
          ))}
        </div>
      </div>

      {/* UI 缩放 */}
      <div className={(activeTab === 'uiscale' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Layout size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">UI 缩放</h2>
        </div>
        <p className="text-xs text-textMuted mb-3">调整界面文字和元素大小</p>
        <div className="flex gap-3">
          {[
            { value: 0.85, label: '小', desc: '紧凑布局' },
            { value: 1.0, label: '中', desc: '默认大小' },
            { value: 1.15, label: '大', desc: '放大显示' },
            { value: 1.3, label: '超大', desc: '超大显示' },
          ].map((s) => (
            <button
              key={s.value}
              onClick={() => setUiScale(s.value)}
              className={`flex-1 px-4 py-3 rounded-card border text-sm font-medium transition-all text-left ${
                uiScale === s.value
                  ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                  : 'border-border bg-canvas text-textSecondary hover:border-primary-500/30'
              }`}
            >
              <div className="font-medium">{s.label}</div>
              <div className="text-3xs mt-0.5 opacity-70">{s.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* 策略模式 */}
      <div id="settings-strategy" className={(activeTab === 'strategy' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Zap size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings.strategy')}</h2>
        </div>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1.5 text-textSecondary">
              {t('settings.autoApproveTimeout')} {autoTimeout}
            </label>
            <input
              type="range"
              min="10"
              max="300"
              step="10"
              value={autoTimeout}
              onChange={(e) => setAutoTimeout(parseInt(e.target.value))}
              className="w-full accent-primary-500"
            />
          </div>
          <div className="flex items-center gap-3">
            <Toggle checked={autoDefault} onChange={setAutoDefault} />
            <span className="text-sm text-textSecondary">{t('settings.autoApproveDefault')}</span>
          </div>
        </div>
      </div>

      {/* Mermaid 图表 */}
      <div id="settings-mermaid" className={(activeTab === 'mermaid' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">Mermaid 图表</h2>
        </div>
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Toggle
              checked={getMermaidSetting('mermaid_collapse', true)}
              onChange={(v) => { try { localStorage.setItem('mermaid_collapse', String(v)); window.location.reload() } catch {} }}
            />
            <span className="text-sm text-textSecondary">默认折叠图表（点击展开）</span>
          </div>
          <div className="flex items-center gap-3">
            <Toggle
              checked={getMermaidSetting('mermaid_collapse_errors', true)}
              onChange={(v) => { try { localStorage.setItem('mermaid_collapse_errors', String(v)); window.location.reload() } catch {} }}
            />
            <span className="text-sm text-textSecondary">渲染出错时也折叠</span>
          </div>
        </div>
      </div>

      {/* 外观主题 */}
      <div id="settings-appearance" className={(activeTab === 'appearance' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Palette size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings.appearance')}</h2>
          <span className="text-3xs px-2 py-0.5 rounded-full bg-mint-400/10 text-mint-400 border border-mint-400/20">{t('settings.instantApplyBadge')}</span>
        </div>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-textPrimary">{t('settings.darkMode')}</p>
            <p className="text-xs text-textMuted mt-0.5">
              {theme === 'dark' ? t('settings.darkModeDesc') : t('settings.lightModeDesc')}
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
      <div id="settings-notifications" className={(activeTab === 'notifications' ? '' : 'hidden') + ' bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16'}>
        <div className="flex items-center gap-2 mb-4">
          <Bell size={18} className="text-primary-400" />
          <h2 className="font-semibold text-textPrimary">{t('settings.notifications')}</h2>
          <span className="text-3xs px-2 py-0.5 rounded-full bg-mint-400/10 text-mint-400 border border-mint-400/20">{t('settings.instantApplyBadge')}</span>
        </div>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-textPrimary">{t('settings.notificationsLabel')}</p>
            <p className="text-xs text-textMuted mt-0.5">{t('settings.notificationsDetailDesc')}</p>
          </div>
          <Toggle checked={notifications} onChange={() => handleNotificationToggle(!notifications)} />
        </div>

        <div className="flex items-center justify-between mt-4 pt-4 border-t border-border/50">
          <div>
            <p className="text-sm font-medium text-textPrimary">{t('settings.notificationsPopupLabel')}</p>
            <p className="text-xs text-textMuted mt-0.5">{t('settings.notificationsPopupDesc')}</p>
          </div>
          <Toggle checked={popupNotifications} onChange={() => handlePopupNotificationToggle(!popupNotifications)} />
        </div>
      </div>

      {/* ── 桌面端设置 ── */}
      {isDesktop() && (
        <div id="settings-desktop" className="bg-surface rounded-card border border-border p-3 md:p-6 scroll-mt-16">
          <div className="flex items-center gap-2 mb-4">
            <Monitor size={18} className="text-primary-400" />
            <h2 className="font-semibold text-textPrimary">{t('settings.desktopSection')}</h2>
            <span className="text-3xs px-2 py-0.5 rounded-full bg-accent-500/10 text-accent-500 border border-accent-500/20">{t('settings.desktopSectionDesc')}</span>
          </div>

          <div className="space-y-4">
            {/* 开机自启动 */}
            <div className="flex items-center justify-between p-3 rounded-card border border-border bg-canvas/50">
              <div>
                <p className="text-sm font-medium text-textPrimary">{t('settings.autoStart')}</p>
                <p className="text-xs text-textMuted mt-0.5">{t('settings.autoStartDesc')}</p>
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
                <p className="text-sm font-medium text-textPrimary">{t('settings.dataLocation')}</p>
              </div>
              <p className="text-xs text-textMuted">{t('settings.dataLocationDesc')}</p>
              <p className="text-xs text-primary-400 font-mono mt-1.5">
                ~/.copree/data/
              </p>
            </div>

            {/* 清理缓存 */}
            <div className="flex items-center justify-between p-3 rounded-card border border-border bg-canvas/50">
              <div>
                <p className="text-sm font-medium text-textPrimary">{t('settings.clearCache')}</p>
                <p className="text-xs text-textMuted mt-0.5">{t('settings.clearCacheDesc')}</p>
              </div>
              <button
                onClick={async () => {
                  try {
                    await invoke('clear_cache')
                    setClearCacheMsg(t('settings.clearCacheSuccess'))
                  } catch { /* Rust 端未实现时静默忽略 */ }
                  setTimeout(() => setClearCacheMsg(''), 3000)
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-control border border-border hover:bg-elevated text-sm text-textSecondary transition-colors"
              >
                <Trash2 size={13} />
                {t('settings.clearCacheBtn')}
              </button>
            </div>
            {clearCacheMsg && (
              <p className="text-xs text-mint-400">{clearCacheMsg}</p>
            )}

            {/* 本地模型管理入口 */}
            <button
              onClick={() => navigate('/local-models')}
              className="w-full flex items-center justify-between p-3 rounded-card border border-border bg-canvas/50 hover:bg-elevated transition-colors text-left"
            >
              <div>
                <div className="flex items-center gap-2">
                  <Cpu size={14} className="text-textMuted" />
                  <p className="text-sm font-medium text-textPrimary">{t('settings.localModelEntry')}</p>
                </div>
                <p className="text-xs text-textMuted mt-0.5 ml-6">{t('settings.localModelEntryDesc')}</p>
              </div>
              <ExternalLink size={14} className="text-textMuted shrink-0" />
            </button>

            {/* 实例连接配置入口 */}
            <button
              onClick={() => navigate('/instance-setup')}
              className="w-full flex items-center justify-between p-3 rounded-card border border-border bg-canvas/50 hover:bg-elevated transition-colors text-left"
            >
              <div>
                <div className="flex items-center gap-2">
                  <Globe size={14} className="text-textMuted" />
                  <p className="text-sm font-medium text-textPrimary">{t('settings.instanceSetupEntry')}</p>
                </div>
                <p className="text-xs text-textMuted mt-0.5 ml-6">{t('settings.instanceSetupEntryDesc')}</p>
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
                  <p className="text-sm font-medium text-textPrimary">{t('settings.buildFactory')}</p>
                  <span className={`text-3xs px-1.5 py-0.5 rounded-full ${
                    buildEnvInstalled
                      ? 'bg-mint-400/10 text-mint-400 border border-mint-400/20'
                      : 'bg-border/30 text-textMuted'
                  }`}>
                    {buildEnvInstalled ? t('settings.buildFactoryInstalled') : t('settings.buildFactoryNotInstalled')}
                  </span>
                </div>
              </button>

              {buildFactoryExpanded && (
                <div className="mt-3 space-y-3">
                  <p className="text-xs text-textMuted">{t('settings.buildFactoryDesc')}</p>

                  {!buildEnvInstalled ? (
                    <div className="bg-canvas rounded-control p-4 border border-border">
                      <div className="flex items-center gap-2 mb-3">
                        <XCircle size={14} className="text-textMuted" />
                        <span className="text-sm text-textPrimary">
                          {t('settings.buildFactoryNotInstalled')}
                        </span>
                      </div>
                      <p className="text-xs text-textMuted mb-3">{t('settings.buildFactoryRequired')}</p>
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
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-control bg-primary-500 text-white text-xs font-medium hover:bg-primary-600 disabled:opacity-30 transition-colors"
                        >
                          {installingEnv ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
                          {installingEnv ? t('settings.buildFactoryInstalling') : t('settings.buildFactoryInstall')}
                        </button>
                        <button className="btn btn-xs btn-outline">
                          {t('settings.buildFactoryViewDocs')}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="bg-canvas rounded-control p-4 border border-border space-y-3">
                      <div className="flex items-center gap-2">
                        <CheckCircle size={14} className="text-mint-400" />
                        <span className="text-sm text-textPrimary">
                          {t('settings.buildFactoryInstalled')}
                        </span>
                        {buildEnvVersion && (
                          <span className="text-xs text-textMuted">{buildEnvVersion}</span>
                        )}
                      </div>

                      {/* 构建目标 */}
                      <div>
                        <p className="text-xs font-medium text-textSecondary mb-2">{t('settings.buildFactoryBuildTarget')}</p>
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
                                title={available ? '' : t('settings.buildFactoryCrossPlatformHint')}
                                className={`px-3 py-1.5 rounded-control text-xs font-medium transition-colors ${
                                  available
                                    ? 'bg-primary-500 text-white hover:bg-primary-600'
                                    : 'bg-border/20 text-textMuted cursor-not-allowed'
                                }`}
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
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-control bg-mint-500 text-white text-xs font-medium hover:bg-mint-400 disabled:opacity-30 transition-colors"
                        >
                          {building ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
                          {building ? t('settings.buildFactoryBuilding') : t('settings.buildFactoryBuild')}
                        </button>
                        <button className="btn btn-xs btn-outline">
                          {t('settings.buildFactoryViewLog')}
                        </button>
                      </div>

                      {lastBuild && (
                        <div className="text-xs text-textMuted">
                          {t('settings.buildFactoryLastBuild')}：{lastBuild}
                        </div>
                      )}

                      {downloadLink && (
                        <div className="text-xs">
                          <span className="text-textMuted">{t('settings.buildFactoryDownloadLink')}：</span>
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

  const renderSaveFooter = () => (
    <div className="sticky bottom-0 z-10 bg-gradient-to-t from-canvas via-canvas/95 to-transparent pt-6 pb-2 px-3 md:px-6">
      {message && (
        <div className={`text-sm px-3 py-2 rounded-card mb-3 ${
          message.includes('失败') || message.includes('错误')
            ? 'bg-rose-500/10 border border-rose-500/20 text-rose-400'
            : 'bg-mint-400/10 border border-mint-400/20 text-mint-400'
        }`}>
          {message}
        </div>
      )}
      <button
        onClick={handleSave}
        disabled={saving}
        className="btn btn-md btn-primary gap-2"
      >
        <Save size={16} />
        {saving ? t('settings.saving') : t('settings.save')}
      </button>
      <p className="text-xs text-textMuted mt-2">{t('settings.saveHint')}</p>
    </div>
  )

  return (
    <div className="h-full flex flex-col bg-canvas">
      {/* 头部 */}
      <div className="px-4 h-14 border-b border-border bg-surface flex items-center gap-2 shrink-0">
        {mobileView === 'detail' ? (
          <button
            onClick={() => setMobileView('list')}
            className="md:hidden p-1.5 -ml-1 rounded-control hover:bg-elevated text-textSecondary transition-colors"
          >
            <ArrowLeft size={18} />
          </button>
        ) : (
          <button
            onClick={() => navigate('/me')}
            className="md:hidden p-1.5 -ml-1 rounded-control hover:bg-elevated text-textSecondary transition-colors"
            title={t('nav.me')}
          >
            <ArrowLeft size={18} />
          </button>
        )}
        <h1 className="font-semibold text-textPrimary text-sm">{t('settings.title')}</h1>
      </div>

      {/* ── 移动端：分类导航列表 ── */}
      {mobileView === 'list' && (
        <div className="md:hidden flex-1 overflow-y-auto p-4 pb-[var(--safe-bottom)] bg-canvas space-y-4">
          {user?.role === 'admin' && (
            <button
              onClick={() => navigate('/admin')}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-card bg-primary-500/10 border border-primary-500/20 text-primary-400 hover:bg-primary-500/15 text-sm font-medium transition-colors"
            >
              <Shield size={16} />
              {t('settings.adminButton')}
            </button>
          )}
          {[...new Set(NAV_SECTIONS.map(s => s.category))].map(cat => {
            const items = NAV_SECTIONS.filter(s => s.category === cat)
            return (
              <div key={cat}>
                <div className="text-xs font-semibold text-textMuted uppercase tracking-wider px-1 mb-1.5">{t(cat)}</div>
                <div className="space-y-1">
                  {items.map(({ id, icon: Icon, labelKey }) => (
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
        <div ref={sidebarRef} className="hidden md:flex shrink-0 relative border-r border-border bg-surface" style={{ width: sidebarWidth }}>
          <div className="overflow-y-auto py-1 w-full">
            {/* 管理员快捷入口 */}
            {user?.role === 'admin' && (
              <button
                onClick={() => navigate('/admin')}
                className="w-full flex items-center gap-2 mx-2 px-2 py-1.5 mb-1 rounded-control bg-primary-500/10 border border-primary-500/20 text-primary-400 hover:bg-primary-500/15 text-sm font-medium transition-colors"
                style={{ width: 'calc(100% - 16px)' }}
              >
                <Shield size={14} />
                {t('settings.adminButton')}
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
                      <div className="px-3 h-7 border-b border-border font-medium text-2xs text-textMuted uppercase tracking-wider flex items-center shrink-0">
                        {t(cat)}
                      </div>
                      <div className="py-0.5">
                        {items.map(({ id, icon: Icon, labelKey }) => (
                          <button
                            key={id}
                            onClick={() => switchTab(id)}
                            className={`w-full flex items-center gap-2 px-3 py-1.5 text-sm transition-colors text-left ${
                              activeTab === id
                                ? 'bg-primary-500/10 text-primary-600 dark:text-primary-400 font-medium border-r-2 border-primary-400'
                                : 'text-textSecondary hover:text-textPrimary hover:bg-elevated border-r-2 border-transparent'
                            }`}
                          >
                            <Icon size={14} className="shrink-0 opacity-70" />
                            <span className="truncate">{t(labelKey)}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )
                })
              })()}
            </nav>
          </div>

          {/* 拖拽手柄 */}
          <div
            className="absolute top-0 -right-1.5 w-1.5 h-full cursor-col-resize hover:bg-primary-400/30 active:bg-primary-400/50 transition-colors z-toast"
            onMouseDown={handleResizeStart}
          />
        </div>

        {/* 内容区域 */}
        <div ref={contentRef} className="flex-1 overflow-y-auto p-4 md:p-6 pb-0 md:pb-0">
          <div className="max-w-2xl mx-auto pb-4">
            {/* 移动端管理员快捷入口 */}
            {user?.role === 'admin' && (
              <button
                onClick={() => navigate('/admin')}
                className="md:hidden w-full flex items-center justify-center gap-2 px-4 py-3 mb-4 rounded-card bg-primary-500/10 border border-primary-500/20 text-primary-400 hover:bg-primary-500/15 text-sm font-medium transition-colors"
              >
                <Shield size={16} />
                {t('settings.adminButton')}
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
            <h3 className="text-lg font-semibold text-textPrimary mb-4">{t('auth.bindEmailTitle')}</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-textSecondary mb-1">{t('auth.email')}</label>
                <input
                  type="email"
                  value={bindEmail}
                  onChange={e => setBindEmail(e.target.value)}
                  className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                  placeholder={t('auth.emailPlaceholder')}
                />
              </div>
              <button
                onClick={handleSendBindCode}
                disabled={!bindEmail || bindSendCooldown > 0}
                className="w-full py-2 text-xs rounded-card border border-border text-textSecondary hover:text-textPrimary hover:bg-elevated disabled:opacity-30 transition-colors"
              >
                {bindSendCooldown > 0 ? `${t('auth.codeResendIn')} ${bindSendCooldown}s` : bindCodeSent ? t('auth.codeSent') : t('auth.sendCode')}
              </button>
              <div>
                <label className="block text-xs text-textSecondary mb-2 text-center">{t('auth.codePlaceholder')}</label>
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
                  className="flex-1 py-2 text-sm border border-border rounded-card hover:bg-elevated text-textSecondary transition-colors font-medium"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleBind}
                  disabled={!bindEmail || !bindCode || bindLoading}
                  className="btn btn-sm btn-primary flex-1"
                >
                  {bindLoading ? t('common.saving') : t('common.confirm')}
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
                <h3 className="text-lg font-semibold text-textPrimary">{t('settings.removeEmailTitle')}</h3>
                <p className="text-sm text-textSecondary mt-1">{t('settings.removeEmailDesc')}</p>
              </div>
            </div>
            <div className="flex gap-3 mt-5">
              <button
                onClick={() => setRemoveConfirm(false)}
                className="flex-1 py-2.5 text-sm border border-border rounded-card hover:bg-elevated text-textSecondary transition-colors font-medium"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleRemoveEmail}
                className="flex-1 py-2.5 text-sm bg-rose-500 text-white rounded-card hover:bg-rose-400 font-medium transition-all"
              >
                {t('common.confirm')}
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
                <h3 className="text-lg font-semibold text-textPrimary">{t('settings.unsavedTitle')}</h3>
                <p className="text-sm text-textSecondary mt-1">{t('settings.unsavedDesc')}</p>
              </div>
            </div>
            <div className="flex gap-3 mt-5">
              <button
                onClick={() => blocker.reset?.()}
                className="flex-1 py-2.5 text-sm border border-border rounded-card hover:bg-elevated text-textSecondary transition-colors font-medium"
              >
                {t('settings.continueEditing')}
              </button>
              <button
                onClick={() => blocker.proceed?.()}
                className="flex-1 py-2.5 text-sm bg-rose-500 text-white rounded-card hover:bg-rose-400 font-medium transition-all"
              >
                {t('settings.discardChanges')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}