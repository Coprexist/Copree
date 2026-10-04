/**
 * 管理员：世界商城 GitHub 同步配置
 * - 仓库（owner/repo）、系统 token（留空=不修改）、自动获取开关
 * - 「测试连接」= 调一次 GitHub 刷新（能成功说明仓库+token 有效）
 */
import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { Github, Save, RefreshCw, CheckCircle, XCircle, Globe, Key } from 'lucide-react'
import ExternalLinkSafe from './ExternalLinkSafe'

interface MarketSettings {
  github_repo: string
  github_token: string  // 管理员视角：脱敏值（前4…后4）
  auto_sync_enabled: boolean
}

export default function MarketGithubTab() {
  const [repo, setRepo] = useState('')
  const [token, setToken] = useState('')
  const [autoSync, setAutoSync] = useState(false)
  const [hasToken, setHasToken] = useState(false)
  const [masked, setMasked] = useState('')  // 当前已配置的脱敏值
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const s = await api.get<MarketSettings>('/market/settings')
      setRepo(s.github_repo || '')
      setHasToken(!!s.github_token)
      setMasked(s.github_token || '')
      setAutoSync(!!s.auto_sync_enabled)
    } catch (e: any) {
      setMsg({ ok: false, text: `加载配置失败: ${e?.message || e}` })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const doSave = async () => {
    setSaving(true)
    setMsg(null)
    try {
      await api.put('/market/settings', {
        github_repo: repo.trim(),
        github_token: token.trim() || undefined,  // 留空 = 不修改
        auto_sync_enabled: autoSync,
      })
      setToken('')
      setMsg({ ok: true, text: '配置已保存' })
      load()
    } catch (e: any) {
      setMsg({ ok: false, text: `保存失败: ${e?.message || e}` })
    } finally {
      setSaving(false)
    }
  }

  const doTest = async () => {
    setTesting(true)
    setMsg(null)
    try {
      const r = await api.post<{ added: number; updated: number; removed: number }>('/market/github/refresh')
      setMsg({ ok: true, text: `连接正常，GitHub 索引已刷新（+${r.added} 新增, ${r.updated} 更新, -${r.removed} 移除）` })
    } catch (e: any) {
      setMsg({ ok: false, text: `连接失败: ${e?.message || e}` })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-textPrimary flex items-center gap-2">
            <Github size={16} className="text-primary-400" /> 世界商城 GitHub 同步
          </h2>
          <p className="text-xs text-textMuted mt-1">
            配置公共仓库后，商城的「GitHub」板块可发布/获取世界资源。用户也可在「我的」页绑定自己的 GitHub，同步时以本人身份推送。
          </p>
        </div>
        <button
          onClick={doTest}
          disabled={testing}
          className="btn btn-sm btn-outline shrink-0"
        >
          <RefreshCw size={12} className={testing ? 'animate-spin' : ''} /> {testing ? '测试中…' : '测试连接'}
        </button>
      </div>

      {msg && (
        <div className={`text-sm px-4 py-3 rounded-card ${msg.ok ? 'text-mint-400 bg-mint-500/10' : 'text-rose-400 bg-rose-500/10'}`}>
          {msg.text}
        </div>
      )}

      <div className="bg-surface border border-border rounded-card p-4 space-y-4 max-w-xl">
        <div>
          <label className="flex items-center gap-1.5 text-xs text-textSecondary mb-1.5">
            <Globe size={11} /> 公共仓库（owner/repo）
          </label>
          <input
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="Coprexist/Copree-Community"
            className="w-full bg-elevated text-sm px-3 py-2 rounded-control border border-border outline-none focus:border-primary-500/50 text-textPrimary"
          />
          <div className="text-3xs text-textMuted mt-1">同步目标仓库；本地用户绑定的 GitHub 也需对该仓库有写权限</div>
        </div>

        <div>
          <label className="flex items-center gap-1.5 text-xs text-textSecondary mb-1.5">
            <Key size={11} /> 系统 Token（classic 或 fine-grained，需仓库写权限）
            <ExternalLinkSafe href="https://github.com/settings/tokens/new" className="text-3xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors shrink-0 ml-auto">去 GitHub 生成 token →</ExternalLinkSafe>
          </label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={hasToken ? `已配置 ${masked}（留空保持不变）` : '未配置'}
            className="w-full bg-elevated text-sm px-3 py-2 rounded-control border border-border outline-none focus:border-primary-500/50 text-textPrimary"
          />
          <div className="text-3xs text-textMuted mt-1">
            {hasToken ? `已配置系统 token（加密存储，仅显示 ${masked}）` : '未配置——用户未绑定 GitHub 时同步将失败'}
          </div>
        </div>

        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={autoSync}
            onChange={(e) => setAutoSync(e.target.checked)}
            className="accent-primary-500 w-4 h-4"
          />
          <span className="text-sm text-textSecondary">启动时自动获取最新（后端启动自动刷新 GitHub 索引）</span>
        </label>

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={doSave}
            disabled={saving || loading}
            className="btn btn-xs btn-primary gap-1.5"
          >
            {saving ? <RefreshCw size={12} className="animate-spin" /> : <Save size={12} />} {saving ? '保存中…' : '保存配置'}
          </button>
          {loading && <span className="text-xs text-textMuted">加载中…</span>}
        </div>
      </div>

      <div className="text-3xs text-textMuted max-w-xl space-y-1">
        <div className="flex items-center gap-1"><CheckCircle size={10} className="text-mint-400" /> 同步身份优先级：用户绑定的 GitHub → 系统 token</div>
        <div className="flex items-center gap-1"><CheckCircle size={10} className="text-mint-400" /> 商城的「GitHub」板块读本地快照，管理员可在此测试连接/刷新</div>
        <div className="flex items-center gap-1"><XCircle size={10} className="text-textMuted" /> Token 仅存于数据库配置，绝不出现在代码与日志</div>
      </div>
    </div>
  )
}
