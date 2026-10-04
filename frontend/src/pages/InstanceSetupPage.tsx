import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useT } from '../i18n/I18nContext'
import { isDesktop, getInstanceUrl } from '../utils/platform'
import { Globe, CheckCircle, XCircle, Loader2, ArrowRight } from 'lucide-react'

export default function InstanceSetupPage() {
  const t = useT()
  const navigate = useNavigate()

  const storedUrl = localStorage.getItem('instance_url') || ''
  const [url, setUrl] = useState(storedUrl)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<'success' | 'fail' | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // Web 端不需要此页面，直接跳走
  if (!isDesktop()) {
    navigate('/chat', { replace: true })
    return null
  }

  const handleTest = async () => {
    const trimmed = url.trim().replace(/\/+$/, '')
    if (!trimmed) {
      setError(t('desktop:testFailed'))
      return
    }
    setTesting(true)
    setTestResult(null)
    setError('')
    try {
      const res = await fetch(`${trimmed}/api/auth/me`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('access_token') || ''}`,
        },
      })
      if (res.ok) {
        setTestResult('success')
      } else if (res.status === 401) {
        // 401 也算连通（只是未登录）
        setTestResult('success')
      } else {
        setTestResult('fail')
        setError(t('desktop:testFailed'))
      }
    } catch {
      setTestResult('fail')
      setError(t('desktop:testFailed'))
    } finally {
      setTesting(false)
    }
  }

  const handleSave = () => {
    const trimmed = url.trim().replace(/\/+$/, '')
    if (!trimmed) {
      setError(t('desktop:testFailed'))
      return
    }
    setSaving(true)
    localStorage.setItem('instance_url', trimmed)
    // 简单 delay 让用户看到反馈
    setTimeout(() => {
      setSaving(false)
      navigate('/chat', { replace: true })
    }, 400)
  }

  const handleSkip = () => {
    navigate('/chat', { replace: true })
  }

  return (
    <div className="h-full flex items-center justify-center bg-canvas">
      <div className="max-w-md w-full px-4">
        <div className="bg-surface border border-border rounded-dialog p-6 shadow-xl">
          {/* 标题 */}
          <div className="flex items-center gap-2 mb-2">
            <Globe size={20} className="text-primary-400" />
            <h2 className="text-lg font-semibold text-textPrimary">
              {t('desktop:instanceSetupTitle')}
            </h2>
          </div>
          <p className="text-sm text-textMuted mb-6">
            {t('desktop:instanceSetupDesc')}
          </p>

          {/* 输入框 */}
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium mb-1.5 text-textSecondary">
                {t('desktop:instanceUrlLabel')}
              </label>
              <input
                type="text"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value)
                  setTestResult(null)
                  setError('')
                }}
                className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                placeholder={t('desktop:instanceUrlPlaceholder')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleTest()
                }}
              />
            </div>

            {/* 错误提示 */}
            {error && (
              <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-card px-3 py-2">
                {error}
              </div>
            )}

            {/* 测试连接按钮 */}
            <button
              onClick={handleTest}
              disabled={testing || !url.trim()}
              className="btn btn-md btn-outline w-full gap-2"
            >
              {testing ? (
                <Loader2 size={16} className="animate-spin" />
              ) : testResult === 'success' ? (
                <CheckCircle size={16} className="text-mint-400" />
              ) : testResult === 'fail' ? (
                <XCircle size={16} className="text-rose-400" />
              ) : null}
              {testing
                ? t('desktop:testing')
                : testResult === 'success'
                  ? t('desktop:testSuccess')
                  : t('desktop:testConnection')}
            </button>
          </div>

          {/* 操作按钮 */}
          <div className="flex gap-3 mt-6">
            <button
              onClick={handleSkip}
              className="btn btn-md btn-outline flex-1 text-textMuted"
            >
              {t('desktop:skip')}
            </button>
            <button
              onClick={handleSave}
              disabled={saving || testResult !== 'success'}
              className="btn btn-md btn-primary flex-1 gap-2"
            >
              {saving ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <ArrowRight size={16} />
              )}
              {t('desktop:saveAndContinue')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
