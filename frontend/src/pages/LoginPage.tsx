import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useT } from '../i18n/I18nContext'
import { cacheLangForUnauth } from '../i18n/I18nContext'
import { api } from '../api/client'
import { useAppVersion } from '../hooks/useAppVersion'
import { isEmbedded } from '../embed/bridge'
import type { ReactNode } from 'react'
import { Loader2, Lock, Mail, MessageCircle, User } from 'lucide-react'
import { AutoHeight, Button, PasswordInput, UnderlineTabs } from '../components/ui'
import VerificationCodeInput from '../components/VerificationCodeInput'
import { useLayoutShift } from '../hooks/useLayoutShift'
import LoginBrand from '../components/LoginBrand'
import { ENTER_TRANSITION_EVENT } from '../components/EnterTransition'

/**
 * 带左侧图标的单行输入：登录页几个字段共用这一处配方。
 *
 * 图标只做视觉锚点（pointer-events-none），不吃点击；给它留位的 padding 是
 * .field-with-icon（无层级规则，utilities 的 pl-* 盖不过，只能在 CSS 里写）。
 */
function IconField({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="relative">
      {/* z-10 不能省：密码框里头还有一层 relative 包裹（PasswordInput 自己的），
          同为定位元素时按 DOM 顺序后绘制的会把图标盖掉——用户名框没这层所以看不出来 */}
      <span className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-textMuted">
        {icon}
      </span>
      {children}
    </div>
  )
}

/** 登录方式对应的 provider 标签 */
const METHOD_LABELS: Record<string, string> = {
  direct: 'auth:methodDirect',
  email_code: 'auth:methodEmail',
  wechat: 'auth:methodWechat',
  qq: 'auth:methodQQ',
}

const SESSION_KEY = 'login_state'

/** 从 sessionStorage 恢复登录流程状态（切换页面不丢失验证码流程） */
function loadSessionState() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    if (!raw) return {}
    return JSON.parse(raw)
  } catch { return {} }
}

/** 保存登录流程状态到 sessionStorage */
function saveSessionState(state: Record<string, any>) {
  try {
    const prev = loadSessionState()
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ ...prev, ...state }))
  } catch { /* 静默失败 */ }
}

/** 清除登录流程状态 */
function clearSessionState() {
  try { sessionStorage.removeItem(SESSION_KEY) } catch { /* 静默失败 */ }
}

export default function LoginPage() {
  // 嵌入模式（DSH iframe）且未登录：不展示自带登录表单（避免双重登录体验），
  // 引导用户回宿主登录；宿主登录后 token 经 ?token= 注入复用。
  if (isEmbedded()) {
    return (
      <div className="flex items-center justify-center h-full min-h-screen bg-canvas px-6">
        <div className="text-center max-w-sm">
          <div className="text-lg font-semibold text-textPrimary mb-2">Copree</div>
          <div className="text-sm text-textSecondary mb-6">请先在宿主应用中登录 Copree，再打开此功能。</div>
          <Button
            variant="primary"
            size="md"
            onClick={() => {
              try {
                window.parent?.postMessage({ source: 'copree-embed', type: 'request-login' }, '*')
              } catch { /* 静默 */ }
            }}
          >
            去登录
          </Button>
        </div>
      </div>
    )
  }

  const saved = loadSessionState()
  const appVersion = useAppVersion()      // 当前部署的版本（来自 /health，见 useAppVersion）
  const [mode, setMode] = useState<'login' | 'register'>(saved.mode || 'login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [hasExistingUsers, setHasExistingUsers] = useState<boolean | null>(null)

  // v0.2.0 邮箱认证（状态从 sessionStorage 恢复，切换页面不丢）
  const [providers, setProviders] = useState<string[]>(['direct'])
  const [requireEmailVerification, setRequireEmailVerification] = useState(false)
  const [loginMethod, setLoginMethod] = useState<string>(saved.loginMethod || 'direct')
  const [email, setEmail] = useState(saved.email || '')
  const [code, setCode] = useState(saved.code || '')
  const [codeSent, setCodeSent] = useState(saved.codeSent || false)
  const [sendCooldown, setSendCooldown] = useState(0)
  const [auditRealIp, setAuditRealIp] = useState(false)

  const { login, register } = useAuth()
  const navigate = useNavigate()
  const t = useT()
  // 提交按钮：过场要从它的位置长出来（见 EnterTransition.tsx）
  const submitRef = useRef<HTMLButtonElement>(null)
  // 品牌区：过场里要按原位再画一份，做到「点下去到紫幕盖满，logo 和标题一动没动」
  const brandRef = useRef<HTMLDivElement>(null)

  // 检查是否已有用户 + 获取登录方式配置
  useEffect(() => {
    api.get<{ has_users: boolean }>('/auth/has-users')
      .then(r => setHasExistingUsers(r.has_users))
      .catch(() => setHasExistingUsers(true))
    // 获取可用的登录方式
    api.get<{ default_language: string; login_providers?: string[]; require_email_verification?: boolean; audit_use_real_ip?: boolean }>('/system/settings')
      .then(r => {
        cacheLangForUnauth(r.default_language === 'zh' ? 'zh' : 'en')
        if (r.login_providers && r.login_providers.length > 0) {
          setProviders(r.login_providers)
          // 默认选第一个可用方式
          if (!r.login_providers.includes(loginMethod)) {
            setLoginMethod(r.login_providers[0])
          }
        }
        setRequireEmailVerification(r.require_email_verification || false)
        setAuditRealIp(r.audit_use_real_ip || false)
      })
      .catch(() => {})
  }, [])

  // 登录流程状态持久化（切换页面/刷新不丢失）
  useEffect(() => {
    saveSessionState({ mode, email, codeSent, code, loginMethod })
  }, [mode, email, codeSent, code, loginMethod])

  // 验证码发送冷却倒计时
  useEffect(() => {
    if (sendCooldown <= 0) return
    const timer = setInterval(() => setSendCooldown(c => c - 1), 1000)
    return () => clearInterval(timer)
  }, [sendCooldown])

  const handleSendCode = useCallback(async () => {
    if (!email || sendCooldown > 0) return
    setError('')
    try {
      const purpose = mode === 'register' ? 'register' : 'login'
      await api.post('/auth/send-verification-code', { email, purpose })
      setCodeSent(true)
      setSendCooldown(60)
    } catch (err: any) {
      setError(err.message || t('common:error'))
    }
  }, [email, sendCooldown, mode, t])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    // 浏览器记住密码/自动填充有时只写 DOM、不派发 input 事件，React 那边看到的还是空串：
    // 于是框里有字、按钮却是"没填完"的样子，点下去还说没填。提交这一刻以表单里的真实值为准。
    const fd = new FormData(e.currentTarget as HTMLFormElement)
    const fromForm = (k: string) => String(fd.get(k) ?? '').trim()
    const u = fromForm('username') || username
    const p = String(fd.get('password') ?? '') || password
    const mail = fromForm('email') || email
    const codeText = code  // 验证码是自绘的分格输入，真值只走 state
    setLoading(true)
    try {
      if (mode === 'login') {
        if (loginMethod === 'email_code') {
          // 邮箱验证码登录
          await login(mail, '', { method: 'email_code', code: codeText })
        } else {
          // 用户名+密码登录
          await login(u, p)
        }
      } else {
        // 注册
        const opts: any = {}
        if (mail) opts.email = mail
        if (codeText) opts.code = codeText
        await register(u, p, opts)
      }
      clearSessionState()
      // 过场从提交按钮长出来：把按钮此刻的位置一起交出去（放大倍数由遮罩按视口算）。
      // 与跳转同一个 tick：遮罩首帧和登录页卸载是同一帧，中间没有裸露的一帧。
      const r = submitRef.current?.getBoundingClientRect()
      const b = brandRef.current?.getBoundingClientRect()
      window.dispatchEvent(new CustomEvent(ENTER_TRANSITION_EVENT, {
        detail: {
          rect: r ? { x: r.left, y: r.top, w: r.width, h: r.height } : undefined,
          brand: b ? { x: b.left, y: b.top, w: b.width, h: b.height } : undefined,
          variant: mode === 'register' ? 'register' : 'login',
        },
      }))
      navigate('/chat')
    } catch (err: any) {
      setError(err.message || t('common:error'))
    } finally {
      setLoading(false)
    }
  }

  const showEmailFields = loginMethod === 'email_code' || (mode === 'register' && requireEmailVerification)
  const showDirectFields = loginMethod === 'direct'
  const isRegister = mode === 'register'
  const isLogin = mode === 'login'

  // 可用的登录方式标签
  const availableMethods = providers.length > 0 ? providers : ['direct']

  // 卡片里的字段增删后，让位置变了的元素平滑滑过去（高度由 AutoHeight 负责）
  const cardScopeRef = useRef<HTMLDivElement>(null)
  useLayoutShift(cardScopeRef, [mode, loginMethod, codeSent, !!error])

  return (
    <div className="min-h-screen flex bg-canvas relative overflow-hidden">
      {/* 背景星点 */}
      <div className="absolute inset-0 pointer-events-none opacity-30"
        style={{
          backgroundImage: `radial-gradient(circle at 25% 30%, rgba(167,139,250,0.08) 0.5px, transparent 0.5px),
                           radial-gradient(circle at 75% 60%, rgba(167,139,250,0.06) 1px, transparent 1px),
                           radial-gradient(circle at 40% 80%, rgba(251,191,36,0.05) 0.8px, transparent 0.8px)`,
          backgroundSize: '80px 80px, 120px 120px, 100px 100px',
        }}
      />

      {/* 左上角版本号：出问题时第一眼要能报出版本（数据来自 /health，见 useAppVersion） */}
      {appVersion && (
        <div className="absolute left-5 top-4 z-20 text-xs font-medium tabular-nums text-textMuted select-all">
          v{appVersion}
        </div>
      )}

      <div className="relative z-10 flex flex-col items-center justify-center w-full px-4">
        {/* 标志：标记抽在 LoginBrand 里，过场用的是同一份（两份各写会漂移） */}
        <div className="mb-10" ref={brandRef}>
          <LoginBrand />
        </div>

        {/* 表单卡片 */}
        <div ref={cardScopeRef} className="w-full max-w-sm">
          <div className="bg-surface border border-border rounded-dialog p-6 shadow-2xl shadow-black/30">
            {/* 登录/注册切换 */}
            <UnderlineTabs
              grow
              className="mb-4"
              items={[
                { key: 'login' as const, label: t('auth:login') },
                { key: 'register' as const, label: t('auth:register') },
              ]}
              value={mode}
              onChange={(m) => { setMode(m); setError('') }}
            />

            {/* 高度随内容平滑收放：AutoHeight 量出内容真实高度、写成像素再过渡（height:auto 不能插值）。
                不用 View Transition——那个只给整块位图做缩放，容器高度会瞬变。 */}
            <AutoHeight duration={320}>
              {/* 登录方式选择（多方式时显示） */}
              {mode === 'login' && availableMethods.length > 1 && (
                <div data-flip="method" className="flex gap-1.5 mb-4 flex-wrap">
                  {availableMethods.map(m => (
                    <Button
                      key={m}
                      variant="outline"
                      size="xs"
                      className={loginMethod === m ? 'is-active' : ''}
                      onClick={() => { setLoginMethod(m); setError('') }}
                    >
                      {t(METHOD_LABELS[m] || 'auth:methodDirect')}
                    </Button>
                  ))}
                </div>
              )}

              <form onSubmit={handleSubmit} className="space-y-4">
                {/* ── 注册模式：邮箱 → 用户名 → 密码 ── */}
                {isRegister && (
                  <>
                    {/* 邮箱（注册时选填，强制验证时必填） */}
                    <div data-flip="email">
                      <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                        {t('auth:email')}
                        {!requireEmailVerification && (
                          <span className="text-textMuted ml-1">{t('auth:emailOptional')}</span>
                        )}
                      </label>
                      <IconField icon={<Mail size={14} />}>
                        <input
                          name="email"
                          type="email"
                          value={email}
                          onChange={(e) => { setEmail(e.target.value); setCodeSent(false) }}
                          required={requireEmailVerification}
                          className="field field-lg field-with-icon w-full"
                          placeholder={t('auth:emailPlaceholder')}
                        />
                      </IconField>
                    </div>

                    {/* 发送验证码（强制验证或已填邮箱时显示） */}
                    {(requireEmailVerification || email) && (
                      <Button
                        data-flip="send"
                        variant="outline"
                        size="md"
                        className="w-full"
                        onClick={handleSendCode}
                        disabled={!email || sendCooldown > 0}
                      >
                        {sendCooldown > 0
                          ? t('auth:codeResendIn').replace('{seconds}', String(sendCooldown))
                          : codeSent ? t('auth:codeSent') : t('auth:sendCode')
                        }
                      </Button>
                    )}

                    {/* 验证码输入 */}
                    {codeSent && (
                      <div data-flip="code">
                        <label className="block text-xs font-medium text-textSecondary mb-2 ml-0.5 text-center">
                          {t('auth:codePlaceholder')}
                        </label>
                        <VerificationCodeInput
                          value={code}
                          onChange={setCode}
                        />
                      </div>
                    )}

                    {/* 用户名 */}
                    <div data-flip="username">
                      <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                        {t('auth:username')}
                      </label>
                      <IconField icon={<User size={14} />}>
                        <input
                          name="username"
                          type="text"
                          value={username}
                          onChange={(e) => setUsername(e.target.value)}
                          required minLength={2}
                          className="field field-lg field-with-icon w-full"
                          placeholder={t('auth:usernamePlaceholder')}
                        />
                      </IconField>
                    </div>

                    {/* 密码 */}
                    <div data-flip="password">
                      <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                        {t('auth:password')}
                      </label>
                      <IconField icon={<Lock size={14} />}>
                        <PasswordInput
                          name="password"
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          required minLength={6}
                          fieldSize="lg"
                          wrapperClassName="w-full"
                          className="field-with-icon"
                          placeholder={'••••••••（' + t('auth:passwordHint') + '）'}
                        />
                      </IconField>
                    </div>
                  </>
                )}

                {/* ── 登录模式 ── */}
                {isLogin && (
                  <>
                    {/* 直接登录：统一输入框（用户名或邮箱） */}
                    {loginMethod === 'direct' && (
                      <>
                        <div data-flip="username">
                          <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                            {t('auth:username')} / {t('auth:email')}
                          </label>
                          <IconField icon={<User size={14} />}>
                            <input
                              name="username"
                              type="text"
                              value={username}
                              onChange={(e) => setUsername(e.target.value)}
                              required
                              className="field field-lg field-with-icon w-full"
                              placeholder={t('auth:usernamePlaceholder')}
                            />
                          </IconField>
                        </div>
                        <div data-flip="password">
                          <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                            {t('auth:password')}
                          </label>
                          <IconField icon={<Lock size={14} />}>
                            <PasswordInput
                              name="password"
                              value={password}
                              onChange={(e) => setPassword(e.target.value)}
                              required minLength={6}
                              fieldSize="lg"
                              wrapperClassName="w-full"
                              className="field-with-icon"
                              placeholder={'••••••••（' + t('auth:passwordHint') + '）'}
                            />
                          </IconField>
                        </div>
                      </>
                    )}

                    {/* 邮箱+验证码登录 */}
                    {loginMethod === 'email_code' && (
                      <>
                        <div data-flip="email">
                          <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                            {t('auth:email')}
                          </label>
                          <IconField icon={<Mail size={14} />}>
                            <input
                              name="email"
                              type="email"
                              value={email}
                              onChange={(e) => { setEmail(e.target.value); setCodeSent(false) }}
                              required
                              className="field field-lg field-with-icon w-full"
                              placeholder={t('auth:emailPlaceholder')}
                            />
                          </IconField>
                        </div>
                        <Button
                          data-flip="send"
                          variant="outline"
                          size="md"
                          className="w-full"
                          onClick={handleSendCode}
                          disabled={!email || sendCooldown > 0}
                        >
                          {sendCooldown > 0
                            ? t('auth:codeResendIn').replace('{seconds}', String(sendCooldown))
                            : codeSent ? t('auth:codeSent') : t('auth:sendCode')
                          }
                        </Button>
                        {codeSent && (
                          <div data-flip="code">
                            <label className="block text-xs font-medium text-textSecondary mb-2 ml-0.5 text-center">
                              {t('auth:codePlaceholder')}
                            </label>
                            <VerificationCodeInput
                              value={code}
                              onChange={setCode}
                            />
                          </div>
                        )}
                      </>
                    )}
                  </>
                )}

                {/* 错误 */}
                {error && (
                  <div className="text-sm text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-card px-3.5 py-2.5">
                    {error}
                  </div>
                )}

                {/* 提交 */}
                <button
                  ref={submitRef}
                  data-flip="submit"
                  type="submit"
                  // 只跟 loading：空字段由 required 在提交时拦（浏览器原生提示），
                  // 用 state 推"填完没有"会被自动填充坑成半透明按钮
                  disabled={loading}
                  className="btn btn-md btn-primary w-full duration-200 hover:shadow-primary-400/30 mt-2"
                >
                  {loading ? (
                    <span className="inline-flex items-center gap-2">
                      <Loader2 size={16} className="animate-spin" />
                      {t('auth:verifying')}
                    </span>
                  ) : (
                    // 主行动文案的切换：旧文案向上滑出淡掉，新文案从下方滑入。
                    // 不走左右平移——「进入平台 / 创建账号」在中英日三种语言里长度都不同，
                    // 横向推会错位、换行或撞边；竖向滑不受字数影响。
                    <span className="relative block h-5 w-full overflow-hidden">
                      <span
                        aria-hidden={!isLogin}
                        className={'absolute inset-0 flex items-center justify-center transition-all duration-300 ease-out '
                          + (isLogin ? 'translate-y-0 opacity-100' : '-translate-y-full opacity-0')}>
                        {t('auth:enterPlatform')}
                      </span>
                      <span
                        aria-hidden={isLogin}
                        className={'absolute inset-0 flex items-center justify-center transition-all duration-300 ease-out '
                          + (isLogin ? 'translate-y-full opacity-0' : 'translate-y-0 opacity-100')}>
                        {t('auth:createAccount')}
                      </span>
                    </span>
                  )}
                </button>
              </form>

              {mode === 'register' && hasExistingUsers === false && (
                <p className="text-xs text-textMuted mt-4 text-center leading-relaxed">
                  {t('auth:firstUserAdminPrefix')}
                  <span className="text-accent-400 font-medium">{t('auth:firstUserAdminHighlight')}</span>
                </p>
              )}
            </AutoHeight>
          </div>

          {auditRealIp && (
            <p className="text-[11px] text-textMuted mt-3 text-center leading-relaxed px-2">
              {t('auth:loginIpNotice')}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
