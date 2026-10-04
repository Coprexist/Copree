import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useT } from '../i18n/I18nContext'
import { cacheLangForUnauth } from '../i18n/I18nContext'
import { api } from '../api/client'
import { isEmbedded } from '../embed/bridge'
import { MessageCircle, Mail } from 'lucide-react'
import VerificationCodeInput from '../components/VerificationCodeInput'

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
          <button
            onClick={() => {
              try {
                window.parent?.postMessage({ source: 'copree-embed', type: 'request-login' }, '*')
              } catch { /* 静默 */ }
            }}
            className="px-5 py-2 rounded-control bg-primary-600 text-white text-sm font-medium hover:bg-primary-500"
          >
            去登录
          </button>
        </div>
      </div>
    )
  }

  const saved = loadSessionState()
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

  const { login, register } = useAuth()
  const navigate = useNavigate()
  const t = useT()

  // 检查是否已有用户 + 获取登录方式配置
  useEffect(() => {
    api.get<{ has_users: boolean }>('/auth/has-users')
      .then(r => setHasExistingUsers(r.has_users))
      .catch(() => setHasExistingUsers(true))
    // 获取可用的登录方式
    api.get<{ default_language: string; login_providers?: string[]; require_email_verification?: boolean }>('/system/settings')
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
    setLoading(true)
    try {
      if (mode === 'login') {
        if (loginMethod === 'email_code') {
          // 邮箱验证码登录
          await login(email, '', { method: 'email_code', code })
        } else {
          // 用户名+密码登录
          await login(username, password)
        }
      } else {
        // 注册
        const opts: any = {}
        if (email) opts.email = email
        if (code) opts.code = code
        await register(username, password, opts)
      }
      clearSessionState()
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

      <div className="relative z-10 flex flex-col items-center justify-center w-full px-4">
        {/* 标志 */}
        <div className="mb-10 text-center">
          <div className="relative inline-flex items-center justify-center mb-6">
            <div className="absolute w-20 h-20 rounded-full ai-pulse-active" />
            <div className="absolute w-16 h-16 rounded-full bg-primary-500/20 blur-xl" />
            <div className="relative w-14 h-14 rounded-dialog flex items-center justify-center shadow-lg shadow-primary-500/25 overflow-hidden">
              <img src="/logo.png" alt="Copree" className="w-full h-full object-contain" />
            </div>
          </div>
          <h1 className="text-2xl font-bold text-textPrimary tracking-tight">{t('auth:title')}</h1>
          <p className="text-sm text-textSecondary mt-2 font-medium">{t('auth:subtitle')}</p>
        </div>

        {/* 表单卡片 */}
        <div className="w-full max-w-sm">
          <div className="bg-surface border border-border rounded-dialog p-6 shadow-2xl shadow-black/30">
            {/* 登录/注册切换 */}
            <div className="flex bg-canvas rounded-control p-1 mb-4 ring-1 ring-border/50">
              <button
                onClick={() => { setMode('login'); setError('') }}
                className={`flex-1 py-2 text-sm font-medium rounded-control transition-all duration-200 ${
                  mode === 'login' ? 'bg-primary-600/20 text-primary-600 dark:text-primary-300 shadow-sm' : 'text-textMuted hover:text-textSecondary'
                }`}
              >
                {t('auth:login')}
              </button>
              <button
                onClick={() => { setMode('register'); setError('') }}
                className={`flex-1 py-2 text-sm font-medium rounded-control transition-all duration-200 ${
                  mode === 'register' ? 'bg-primary-600/20 text-primary-600 dark:text-primary-300 shadow-sm' : 'text-textMuted hover:text-textSecondary'
                }`}
              >
                {t('auth:register')}
              </button>
            </div>

            {/* 登录方式选择（多方式时显示） */}
            {mode === 'login' && availableMethods.length > 1 && (
              <div className="flex gap-1.5 mb-4 flex-wrap">
                {availableMethods.map(m => (
                  <button
                    key={m}
                    onClick={() => { setLoginMethod(m); setError('') }}
                    className={`text-xs px-2.5 py-1 rounded-full font-medium transition-colors ${
                      loginMethod === m ? 'bg-primary-500/15 text-primary-500' : 'text-textMuted hover:text-textSecondary'
                    }`}
                  >
                    {t(METHOD_LABELS[m] || 'auth:methodDirect')}
                  </button>
                ))}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              {/* ── 注册模式：邮箱 → 用户名 → 密码 ── */}
              {isRegister && (
                <>
                  {/* 邮箱（注册时选填，强制验证时必填） */}
                  <div>
                    <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                      {t('auth:email')}
                      {!requireEmailVerification && (
                        <span className="text-textMuted ml-1">{t('auth:emailOptional')}</span>
                      )}
                    </label>
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => { setEmail(e.target.value); setCodeSent(false) }}
                      required={requireEmailVerification}
                      className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/60 focus:border-primary-500/40 text-sm transition-shadow"
                      placeholder={t('auth:emailPlaceholder')}
                    />
                  </div>

                  {/* 发送验证码（强制验证或已填邮箱时显示） */}
                  {(requireEmailVerification || email) && (
                    <button
                      type="button"
                      onClick={handleSendCode}
                      disabled={!email || sendCooldown > 0}
                      className="w-full py-2 text-sm font-medium rounded-card border border-primary-500/30 text-primary-500 hover:bg-primary-500/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                      {sendCooldown > 0
                        ? t('auth:codeResendIn').replace('{seconds}', String(sendCooldown))
                        : codeSent ? t('auth:codeSent') : t('auth:sendCode')
                      }
                    </button>
                  )}

                  {/* 验证码输入 */}
                  {codeSent && (
                    <div>
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
                  <div>
                    <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                      {t('auth:username')}
                    </label>
                    <input
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      required minLength={2}
                      className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/60 focus:border-primary-500/40 text-sm transition-shadow"
                      placeholder={t('auth:usernamePlaceholder')}
                    />
                  </div>

                  {/* 密码 */}
                  <div>
                    <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                      {t('auth:password')}
                    </label>
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required minLength={6}
                      className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/60 focus:border-primary-500/40 text-sm transition-shadow"
                      placeholder={'••••••••（' + t('auth:passwordHint') + '）'}
                    />
                  </div>
                </>
              )}

              {/* ── 登录模式 ── */}
              {isLogin && (
                <>
                  {/* 直接登录：统一输入框（用户名或邮箱） */}
                  {loginMethod === 'direct' && (
                    <>
                      <div>
                        <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                          {t('auth:username')} / {t('auth:email')}
                        </label>
                        <input
                          type="text"
                          value={username}
                          onChange={(e) => setUsername(e.target.value)}
                          required
                          className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/60 focus:border-primary-500/40 text-sm transition-shadow"
                          placeholder={t('auth:usernamePlaceholder')}
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                          {t('auth:password')}
                        </label>
                        <input
                          type="password"
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          required minLength={6}
                          className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/60 focus:border-primary-500/40 text-sm transition-shadow"
                          placeholder={'••••••••（' + t('auth:passwordHint') + '）'}
                        />
                      </div>
                    </>
                  )}

                  {/* 邮箱+验证码登录 */}
                  {loginMethod === 'email_code' && (
                    <>
                      <div>
                        <label className="block text-xs font-medium text-textSecondary mb-1.5 ml-0.5">
                          {t('auth:email')}
                        </label>
                        <input
                          type="email"
                          value={email}
                          onChange={(e) => { setEmail(e.target.value); setCodeSent(false) }}
                          required
                          className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/60 focus:border-primary-500/40 text-sm transition-shadow"
                          placeholder={t('auth:emailPlaceholder')}
                        />
                      </div>
                      <button
                        type="button"
                        onClick={handleSendCode}
                        disabled={!email || sendCooldown > 0}
                        className="w-full py-2 text-sm font-medium rounded-card border border-primary-500/30 text-primary-500 hover:bg-primary-500/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      >
                        {sendCooldown > 0
                          ? t('auth:codeResendIn').replace('{seconds}', String(sendCooldown))
                          : codeSent ? t('auth:codeSent') : t('auth:sendCode')
                        }
                      </button>
                      {codeSent && (
                        <div>
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
                type="submit"
                disabled={loading || (isLogin && loginMethod === 'direct' && (!username || !password)) || (isLogin && loginMethod === 'email_code' && (!email || !code)) || (isRegister && !username)}
                className="btn btn-md btn-primary w-full duration-200 hover:shadow-primary-400/30 mt-2"
              >
                {loading ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    {t('auth:verifying')}
                  </span>
                ) : isLogin ? t('auth:enterPlatform') : t('auth:createAccount')}
              </button>
            </form>

            {mode === 'register' && hasExistingUsers === false && (
              <p className="text-xs text-textMuted mt-4 text-center leading-relaxed">
                {t('auth:firstUserAdminPrefix')}
                <span className="text-accent-400 font-medium">{t('auth:firstUserAdminHighlight')}</span>
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
