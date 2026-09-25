import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, FileArchive, RefreshCw, Trash2, Upload } from 'lucide-react'
import { api } from '../../../api/client'
import { useT } from '../../../i18n/I18nContext'
import { useAuth } from '../../../context/AuthContext'
import { Button, Dialog, EmptyState, Input, confirmAsync } from '../../../components/ui'
import { CATEGORY_ICON, CATEGORY_LABEL_KEY } from '../../../utils/pluginCategories'

/**
 * 总商城 · 插件商城分区（一期：本地安装包）
 *
 * 排版约定：浏览用卡片（像商店），已安装的增删改在「已安装」分区用表格（像管理页）——
 * 两种心智不混在一页。
 *
 * 信任分层：内置 / 已验证 = 我们自己审过；社区 = CI 通过即收录；本分区的包属于「未收录」，
 * 由管理员上传。plugin.py 装上去就是可执行代码，所以安装前先把 manifest 亮出来。
 */
interface StoreManifest {
  id: string
  name: string
  description: string
  category: string
  version: string
  author: string
  entry: string
}

interface StorePackage {
  file_name: string
  error?: string
  size?: number
  manifest?: StoreManifest
  file_count?: number
  uncompressed_bytes?: number
  sha256?: string
  files?: string[]
  installed?: boolean
  installed_version?: string | null
  builtin_conflict?: boolean
  updatable?: boolean
}

const fmtSize = (n: number) =>
  n >= 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + 'MB' : Math.max(1, Math.round(n / 1024)) + 'KB'

const STATUS_CLASS: Record<string, string> = {
  mint: 'text-mint-400',
  muted: 'text-textMuted',
  accent: 'text-amber-600 dark:text-amber-400',
  rose: 'text-rose-400',
}

export default function PluginMarketSection() {
  const t = useT()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [packages, setPackages] = useState<StorePackage[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const [keyword, setKeyword] = useState('')
  const [detail, setDetail] = useState<StorePackage | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api.get<{ packages: StorePackage[] }>('/plugins/store')
      setPackages(r.packages || [])
    } catch (e: any) {
      setMsg({ tone: 'err', text: e?.message || String(e) })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  /** 所有动作共用一个出口：忙标记、提示、成功后重拉列表 */
  const act = async (key: string, fn: () => Promise<any>, okText: string) => {
    setBusy(key)
    setMsg(null)
    try {
      await fn()
      setMsg({ tone: 'ok', text: okText })
      await load()
    } catch (e: any) {
      setMsg({ tone: 'err', text: e?.message || String(e) })
    } finally {
      setBusy('')
    }
  }

  const upload = (file: File) =>
    act('upload', () => api.upload('/plugins/store/packages', file), t('tool:store.uploadSuccess'))

  const install = (pkg: StorePackage) => {
    const name = pkg.manifest?.name || pkg.file_name
    const url = '/plugins/store/packages/' + encodeURIComponent(pkg.file_name) + '/install?upgrade=' + (pkg.installed ? 'true' : 'false')
    return act('install:' + pkg.file_name, () => api.post(url),
      pkg.installed ? t('tool:store.upgradeSuccess', { name }) : t('tool:store.installSuccess', { name }))
  }

  const uninstall = async (pkg: StorePackage) => {
    const id = pkg.manifest?.id || ''
    const ok = await confirmAsync({
      title: t('tool:store.uninstall'),
      message: t('tool:store.confirmUninstall', { name: pkg.manifest?.name || id }),
      danger: true,
    })
    if (!ok) return
    await act('uninstall:' + id, () => api.delete('/plugins/store/installed/' + encodeURIComponent(id)),
      t('tool:store.uninstallSuccess', { name: pkg.manifest?.name || id }))
  }

  const removePackage = async (pkg: StorePackage) => {
    const ok = await confirmAsync({
      title: t('tool:store.deletePackage'),
      message: t('tool:store.confirmDeletePackage', { name: pkg.file_name }),
      danger: true,
    })
    if (!ok) return
    await act('delete:' + pkg.file_name, () => api.delete('/plugins/store/packages/' + encodeURIComponent(pkg.file_name)),
      t('tool:store.deleteSuccess'))
  }

  const statusOf = (pkg: StorePackage): { tone: string; text: string } => {
    if (pkg.error) return { tone: 'rose', text: t('tool:store.brokenPackage') }
    if (pkg.builtin_conflict) return { tone: 'accent', text: t('tool:store.builtinConflict') }
    if (pkg.updatable) return { tone: 'accent', text: pkg.installed_version + ' → ' + pkg.manifest?.version }
    if (pkg.installed) return { tone: 'mint', text: t('tool:store.installed') + ' ' + (pkg.installed_version || '') }
    return { tone: 'muted', text: t('tool:store.notInstalled') }
  }

  const q = keyword.trim().toLowerCase()
  const list = packages.filter(p => {
    if (!q) return true
    const m = p.manifest
    return [p.file_name, m?.id, m?.name, m?.author, m?.description]
      .some(v => (v || '').toLowerCase().includes(q))
  })

  // 这里只可能出现在控制台里，但后端才是权威：非管理员直接给一段说明，别渲染一堆点不动的按钮
  if (!isAdmin) {
    return <EmptyState title={t('tool:store.needAdmin')} description={t('tool:store.readonlyHint')} />
  }

  return (
    <div className="space-y-3">
      {/* 工具条：搜索 + 刷新 + 上传（上传只对管理员开放，普通用户看得见列表、装不了） */}
      <div className="flex items-center gap-2 flex-wrap">
        <Input value={keyword} onChange={e => setKeyword(e.target.value)} placeholder={t('tool:store.searchPackages')} className="w-56" />
        <Button size="sm" variant="secondary" icon={<RefreshCw size={13} />} onClick={load} disabled={loading}>
          {t('tool:store.refresh')}
        </Button>
        <span className="text-xs text-textMuted">{t('tool:store.packageCount', { n: list.length })}</span>
        <label className="btn btn-sm btn-primary gap-1.5 cursor-pointer ml-auto">
            <Upload size={13} /> {t('tool:store.uploadPackage')}
            <input
              type="file"
              accept=".zip"
              className="hidden"
              onChange={e => {
                const f = e.target.files?.[0]
                if (f) upload(f)
                e.target.value = ''
              }}
            />
        </label>
      </div>

      {/* 提示条：成功/失败共用一个位置，避免 toast 与横幅各写一套 */}
      {msg && (
        <div className={'flex items-center gap-2 text-xs rounded-control border px-3 py-2 ' + (msg.tone === 'ok' ? 'border-mint-500/30 bg-mint-500/10 text-mint-400' : 'border-rose-500/30 bg-rose-500/10 text-rose-400')}>
          {msg.tone === 'ok' ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
          <span className="min-w-0 truncate">{msg.text}</span>
        </div>
      )}

      {loading && packages.length === 0 ? (
        <div className="text-xs text-textMuted py-8 text-center">{t('tool:store.loading')}</div>
      ) : list.length === 0 ? (
        <EmptyState
          title={t('tool:store.empty')}
          description={t('tool:store.emptyHintAdmin')}
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {list.map(pkg => {
            const m = pkg.manifest
            const Icon = CATEGORY_ICON[m?.category || 'other'] || FileArchive
            const status = statusOf(pkg)
            const busyKey = 'install:' + pkg.file_name
            return (
              <div key={pkg.file_name} className="flex flex-col gap-2 rounded-card border border-border bg-surface p-3">
                <div className="flex items-start gap-2">
                  <div className="w-9 h-9 rounded-control bg-primary-500/10 flex items-center justify-center shrink-0">
                    <Icon size={17} className="text-primary-400" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium text-textPrimary truncate">{m?.name || pkg.file_name}</span>
                      {m && <span className="text-3xs px-1.5 py-0.5 rounded bg-elevated text-textMuted shrink-0">v{m.version}</span>}
                    </div>
                    <div className="text-3xs text-textMuted mt-0.5 truncate">
                      {m?.author ? m.author + ' · ' : ''}
                      {m ? t(CATEGORY_LABEL_KEY[m.category] || CATEGORY_LABEL_KEY.other) : ''}
                      {' · '}
                      {t('tool:store.uncollected')}
                    </div>
                  </div>
                </div>

                <div className="text-xs text-textSecondary line-clamp-2 min-h-[2em]">
                  {pkg.error ? pkg.error : (m?.description || t('tool:store.noDescription'))}
                </div>

                <div className="text-3xs flex items-center gap-2 flex-wrap">
                  <span className={STATUS_CLASS[status.tone]}>{status.text}</span>
                  <span className="text-textMuted">
                    {pkg.file_count != null ? t('tool:store.fileCount', { n: pkg.file_count }) : fmtSize(pkg.size || 0)}
                  </span>
                </div>

                <div className="flex items-center gap-1.5 mt-auto pt-1 border-t border-border/50">
                  <Button size="sm" variant="ghost" onClick={() => setDetail(pkg)}>{t('tool:store.detail')}</Button>
                  {!pkg.error && (
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busy === busyKey}
                      onClick={() => install(pkg)}
                    >
                      {pkg.updatable || pkg.installed ? t('tool:store.upgrade') : t('tool:store.install')}
                    </Button>
                  )}
                  {pkg.installed && (
                    <Button size="sm" variant="secondary" disabled={busy.startsWith('uninstall:')} onClick={() => uninstall(pkg)}>
                      {t('tool:store.uninstall')}
                    </Button>
                  )}
                  <button
                      onClick={() => removePackage(pkg)}
                      className="ml-auto p-1.5 rounded-control text-textMuted hover:text-rose-400 transition-colors"
                      title={t('tool:store.deletePackage')}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 详情抽屉：安装前把 manifest 摊开（作者/版本/配置入口/校验值/文件清单） */}
      {detail && (
        <Dialog layer="drawer" className="flex justify-end" onClose={() => setDetail(null)}>
          <div className="h-full w-full max-w-md bg-surface border-l border-border shadow-2xl overflow-y-auto">
            <div className="sticky top-0 bg-surface border-b border-border px-4 py-3 flex items-center justify-between">
              <span className="text-sm font-semibold text-textPrimary truncate">
                {detail.manifest?.name || detail.file_name}
              </span>
              <button onClick={() => setDetail(null)} className="text-textMuted hover:text-textPrimary text-xs">
                {t('tool:store.close')}
              </button>
            </div>
            <div className="p-4 space-y-3 text-xs">
              {detail.error ? (
                <div className="text-rose-400">{detail.error}</div>
              ) : (
                <>
                  <div className="text-textSecondary whitespace-pre-wrap">{detail.manifest?.description || t('tool:store.noDescription')}</div>
                  <dl className="grid grid-cols-[5.5rem_1fr] gap-y-1.5">
                    <dt className="text-textMuted">{t('tool:store.fieldId')}</dt><dd className="text-textPrimary break-all">{detail.manifest?.id}</dd>
                    <dt className="text-textMuted">{t('tool:store.fieldVersion')}</dt><dd className="text-textPrimary">{detail.manifest?.version}</dd>
                    <dt className="text-textMuted">{t('tool:store.fieldAuthor')}</dt><dd className="text-textPrimary">{detail.manifest?.author || '—'}</dd>
                    <dt className="text-textMuted">{t('tool:store.fieldCategory')}</dt>
                    <dd className="text-textPrimary">{t(CATEGORY_LABEL_KEY[detail.manifest?.category || 'other'] || CATEGORY_LABEL_KEY.other)}</dd>
                    <dt className="text-textMuted">{t('tool:store.fieldEntry')}</dt><dd className="text-textPrimary break-all">{detail.manifest?.entry || '—'}</dd>
                    <dt className="text-textMuted">{t('tool:store.fieldSize')}</dt>
                    <dd className="text-textPrimary">{fmtSize(detail.uncompressed_bytes || 0)} · {t('tool:store.fileCount', { n: detail.file_count || 0 })}</dd>
                    <dt className="text-textMuted">{t('tool:store.fieldSha')}</dt>
                    <dd className="text-textMuted break-all font-mono">{detail.sha256}</dd>
                  </dl>
                  {detail.files && detail.files.length > 0 && (
                    <div>
                      <div className="text-textMuted mb-1">{t('tool:store.fieldFiles')}</div>
                      <div className="font-mono text-3xs text-textSecondary max-h-48 overflow-y-auto bg-elevated rounded-control p-2">
                        {detail.files.map(f => <div key={f}>{f}</div>)}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </Dialog>
      )}
    </div>
  )
}
