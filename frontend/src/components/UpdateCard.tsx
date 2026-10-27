/**
 * 版本与更新卡片（管理面板「通用」页）。
 *
 * 网页版（docker/源码部署）与桌面版共用这一张卡，区别只在「怎么升到这个版本」：
 * - docker：给 compose 命令（复制即用）；
 * - desktop：给安装包直链（后端按本机平台挑好的那个资产）；
 * - source：给 git pull。
 *
 * 判定全部来自 /api/system/update-status，卡片自己不算版本号——比较逻辑只有
 * utils/pure/version.py 一处，前端重复实现一次就是两套规则。
 */
import { useState } from 'react'
import { Check, Copy, Download, ExternalLink, RefreshCw } from 'lucide-react'
import Card from './ui/Card'
import Button from './ui/Button'
import Badge from './ui/Badge'
import { useT, useLang } from '../i18n/I18nContext'
import { getLangMeta } from '../i18n/languages'
import { useRuntimeInfo } from '../hooks/useAppVersion'
import { useUpdateStatus } from '../hooks/useUpdateStatus'
import { copyText } from '../utils/clipboard'
import { formatRelativeTime } from '../utils/time'

const DOCKER_CMD = 'docker compose pull && docker compose up -d'
/** 上游没给出链接时的兜底（离线检查失败时仍能手动去发布页看一眼） */
const RELEASES_PAGE = 'https://github.com/Coprexist/Copree-Releases/releases/latest'
const SOURCE_CMD = 'git pull && docker compose up -d --build'

function errorText(code: string, t: (key: string) => string): string {
  switch (code) {
    case 'timeout': return t('update:errorTimeout')
    case 'unreachable': return t('update:errorUnreachable')
    case 'rate_limited': return t('update:errorRateLimited')
    case 'not_found': return t('update:errorNotFound')
    case 'bad_response': return t('update:errorBadResponse')
    case 'disabled': return t('update:errorDisabled')
    default: return t('update:errorGeneric')
  }
}

/** 一行命令 + 复制按钮：升级指引里唯一需要用户动手的地方，别让他手抄 */
function CommandRow({ command }: { command: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)

  return (
    <div className="flex items-center gap-2 rounded-control border border-border bg-elevated px-3 py-2">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre font-mono text-xs text-textSecondary">
        {command}
      </code>
      <button
        type="button"
        onClick={async () => {
          if (await copyText(command)) {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }
        }}
        className="inline-flex shrink-0 items-center gap-1 text-3xs text-textMuted transition-colors hover:text-textPrimary"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {copied ? t('update:copied') : t('update:copy')}
      </button>
    </div>
  )
}

function VersionBlock({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-3xs uppercase tracking-wide text-textMuted">{label}</div>
      <div className={'text-sm font-semibold tabular-nums ' + (highlight ? 'text-accent-400' : 'text-textPrimary')}>
        {value}
      </div>
    </div>
  )
}

export default function UpdateCard({ className = '' }: { className?: string }) {
  const t = useT()
  const lang = useLang()
  const { version, distribution } = useRuntimeInfo()
  const { status, loading, failed, refresh } = useUpdateStatus(true)

  const current = status?.current || version
  const hasUpdate = !!status?.has_update
  const disabled = !!status && !status.enabled
  const unreachable = !!status && !status.ok && !disabled

  let badge = <Badge tone="muted">{t('update:checking')}</Badge>
  if (disabled) badge = <Badge tone="muted">{t('update:disabled')}</Badge>
  else if (hasUpdate) badge = <Badge tone="accent">{t('update:updateAvailable')}</Badge>
  else if (status?.ok) badge = <Badge tone="mint">{t('update:upToDate')}</Badge>
  else if (unreachable || failed) badge = <Badge tone="muted">{t('update:cannotCheck')}</Badge>

  const openExternal = (url: string) => {
    if (url) window.open(url, '_blank', 'noopener,noreferrer')
  }

  return (
    <Card
      className={className}
      title={t('update:title')}
      icon={<RefreshCw size={16} className="text-primary-400" />}
      hint={t('update:hint')}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <VersionBlock label={t('update:current')} value={current ? 'v' + current : '—'} />
        <span className="text-textMuted">→</span>
        <VersionBlock
          label={t('update:latestLabel')}
          value={status?.latest ? 'v' + status.latest : '—'}
          highlight={hasUpdate}
        />
        <span className="ml-1">{badge}</span>
      </div>

      <div className="space-y-1 text-xs text-textMuted">
        {status?.published_at && (
          <div>{t('update:publishedAt', { date: new Date(status.published_at).toLocaleDateString(getLangMeta(lang).locale) })}</div>
        )}
        {status?.checked_at && (
          <div>{t('update:checkedAt', { time: formatRelativeTime(status.checked_at, lang) })}</div>
        )}
        {status?.channel === 'beta' && <div>{t('update:channelBeta')}</div>}
        {status?.stale && <div className="text-accent-400">{t('update:staleHint')}</div>}
        {unreachable && <div>{errorText(status.error, t)}</div>}
        {disabled && <div>{t('update:disabledHint')}</div>}
        {failed && !status && <div>{t('update:cannotCheck')}</div>}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          icon={<RefreshCw size={14} />}
          loading={loading}
          onClick={() => void refresh()}
        >
          {loading ? t('update:checking') : t('update:checkNow')}
        </Button>
        {hasUpdate && distribution === 'desktop' && status?.download_url && (
          <Button
            size="sm"
            variant="primary"
            icon={<Download size={14} />}
            onClick={() => openExternal(status.download_url)}
          >
            {t('update:download')}
          </Button>
        )}
        {/* 打不开上游时也要给一条路：至少让管理员能手工去发布页看 */}
        <Button
          size="sm"
          variant="ghost"
          icon={<ExternalLink size={14} />}
          onClick={() => openExternal(status?.release_url || RELEASES_PAGE)}
        >
          {t('update:viewRelease')}
        </Button>
      </div>

      {hasUpdate && (
        <div className="space-y-2 rounded-control border border-border bg-surface/60 p-3">
          <div className="text-xs font-medium text-textSecondary">{t('update:upgradeTitle')}</div>
          {distribution === 'desktop' ? (
            <p className="text-xs text-textMuted">{t('update:upgradeDesktop')}</p>
          ) : distribution === 'docker' ? (
            <>
              <p className="text-xs text-textMuted">{t('update:upgradeDocker')}</p>
              <CommandRow command={DOCKER_CMD} />
            </>
          ) : (
            <>
              <p className="text-xs text-textMuted">{t('update:upgradeSource')}</p>
              <CommandRow command={SOURCE_CMD} />
            </>
          )}
        </div>
      )}

      {status?.notes && (
        <details className="text-xs">
          <summary className="cursor-pointer select-none text-textMuted transition-colors hover:text-textPrimary">
            {t('update:releaseNotes')}
          </summary>
          <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-control bg-elevated p-3 font-sans text-xs text-textSecondary">
            {status.notes}
          </pre>
        </details>
      )}
    </Card>
  )
}
