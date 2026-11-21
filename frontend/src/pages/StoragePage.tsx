/**
 * 存储管理页
 *
 * 按 §3.1 的三块并列：我的文件 / 我在对话里产生的 / 浏览过的。
 * 三块是**并列**关系，不是一个集合的三种筛选——所以各拉各的列表，不合并成一个再分组。
 * 只有「我的文件」计入用量上限：它删掉才真释放；另两块的数字只做说明。
 *
 * 三块收在同一张卡里用下划线页签切换（平台 UnderlineTabs）：它们回答的是同一个问题
 * 「我的东西在哪」，切成三张上下堆叠的卡会让页面变长，也看不出它们并列。
 */
import { Fragment, useState, useEffect, useRef, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { HardDrive, FileText, Loader2, Forward, Upload } from 'lucide-react'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import FilePreviewModal from '../components/FilePreviewModal'
import ForwardFileModal from '../components/ForwardFileModal'
import { PageShell, Card, Button, Badge, UnderlineTabs, type UnderlineTabItem } from '../components/ui'
import { formatFileSize } from '../utils/format'

type Scope = 'mine' | 'produced' | 'viewed'

interface FileItem {
  id: number
  path: string
  size: number
  mime_type: string
  owner_type?: string
  owner_id?: number
  /** 只有「浏览过的」会带：字节还在盘上就 true，已被清理就是 false（条目保留、打不开） */
  is_available?: boolean
  /** 非空＝这一条到点会被回收（「上传了但没发」，§3.5）；值为无时区 UTC */
  reclaim_after?: string | null
}

interface StorageInfo {
  total_used: number
  total_files: number
  quota_mb: number
  usage_percent: number
  /** 「我在对话里产生的」那一栏的字节/条数（不占配额，§7.1） */
  chat_used: number
  chat_files: number
}

/** 每个页签配一句话：说明它是什么、删了会怎样（§7.1） */
const HINT_KEY: Record<Scope, string> = {
  mine: 'me:storageMineHint',
  produced: 'me:storageProducedHint',
  viewed: 'me:storageViewedHint',
}

const fileName = (path: string) => path.split('/').pop() || path
/** 新上传的排在前面：id 自增，比解析 created_at 字符串可靠 */
const newestFirst = (a: FileItem, b: FileItem) => b.id - a.id

/**
 * 还有几小时被回收。
 * 后端给的是无时区 UTC（"2026-11-27 23:23:14.9"）——直接 new Date 会当成本地时间，
 * 差出一整个时区，所以补 Z 再解析。向上取整、最小 1：显示「0 小时后」比不显示更吓人。
 */
function hoursLeft(utc: string): number {
  const at = Date.parse(utc.endsWith('Z') ? utc : `${utc.replace(' ', 'T')}Z`)
  if (Number.isNaN(at)) return 0
  return Math.max(1, Math.ceil((at - Date.now()) / 3_600_000))
}

/** 一行文件：三块共用的唯一行结构 */
function FileRow({
  file,
  unavailable = false,
  onOpen,
  trailing,
}: {
  file: FileItem
  unavailable?: boolean
  onOpen?: () => void
  trailing?: ReactNode
}) {
  const t = useT()
  return (
    <li className="group flex items-center gap-3 h-10 px-2 -mx-2 rounded-control hover:bg-elevated transition-colors">
      <button
        type="button"
        onClick={onOpen}
        disabled={!onOpen}
        className="flex items-center gap-3 min-w-0 flex-1 text-left disabled:cursor-default"
      >
        <FileText size={14} className={`shrink-0 ${unavailable ? 'text-textMuted/40' : 'text-textMuted'}`} />
        <span className={`text-xs truncate flex-1 ${unavailable ? 'text-textMuted' : 'text-textPrimary'}`}>
          {fileName(file.path)}
        </span>
        {unavailable && <Badge tone="muted">{t('me:storageCleaned')}</Badge>}
      </button>
      {/* 没发出去的附件到点会被清掉：说清楚还剩多久，别让文件某天自己消失（§3.5） */}
      {file.reclaim_after && (
        <span className="text-3xs text-textMuted tabular-nums shrink-0">
          {/* 存量宽限是 7 天，写成「168 小时后」没人看得懂：超过两天就按天说 */}
          {hoursLeft(file.reclaim_after) >= 48
            ? t('me:storageClearsInDays', { days: Math.ceil(hoursLeft(file.reclaim_after) / 24) })
            : t('me:storageClearsIn', { hours: hoursLeft(file.reclaim_after) })}
        </span>
      )}
      <span className="text-3xs text-textMuted tabular-nums shrink-0">{formatFileSize(file.size)}</span>
      {trailing}
    </li>
  )
}

/** 一块的列表：超过 limit 条先只铺前 limit 条，剩下的折起来（浏览过的会有上百条） */
function FileList({
  files,
  renderRow,
  limit = 20,
}: {
  files: FileItem[]
  renderRow: (file: FileItem) => ReactNode
  limit?: number
}) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? files : files.slice(0, limit)

  if (files.length === 0) {
    return <p className="text-xs text-textMuted text-center py-6">{t('me:storageNoFiles')}</p>
  }
  return (
    <>
      <ul className="-my-1">
        {shown.map((f) => (
          <Fragment key={f.id}>{renderRow(f)}</Fragment>
        ))}
      </ul>
      {!expanded && files.length > limit && (
        <Button variant="link" size="xs" onClick={() => setExpanded(true)}>
          {t('me:storageShowAll')} {files.length}
        </Button>
      )}
    </>
  )
}

export default function StoragePage() {
  const t = useT()
  const navigate = useNavigate()

  const [scope, setScope] = useState<Scope>('mine')
  const [storage, setStorage] = useState<StorageInfo | null>(null)
  const [mine, setMine] = useState<FileItem[]>([])
  const [produced, setProduced] = useState<FileItem[]>([])
  const [viewed, setViewed] = useState<FileItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [forwardFile, setForwardFile] = useState<FileItem | null>(null)
  const [previewFile, setPreviewFile] = useState<FileItem | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 四个请求一起发：三块列表 + 一份用量。页签切换只是换着看，不该再等一次请求
  const load = async () => {
    const [s, m, p, v] = await Promise.all([
      api.get<StorageInfo>('/user/storage'),
      api.get<FileItem[]>('/fs/list?scope=mine'),
      api.get<FileItem[]>('/fs/list?scope=produced'),
      api.get<FileItem[]>('/fs/list?scope=viewed'),
    ])
    setStorage(s)
    setMine(Array.isArray(m) ? m : [])
    setProduced(Array.isArray(p) ? p : [])
    setViewed(Array.isArray(v) ? v : [])
  }

  useEffect(() => {
    load()
      .catch((e) => setError(e?.message || t('common:error')))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const form = new FormData()
      form.append('file', file)
      // 目录不由前端选：存储页上传进的是「我的文件」（users/{uid}/files）
      await api.post('/fs/upload', form)
      await load()
    } catch {}
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-textMuted" />
      </div>
    )
  }

  const percent = storage?.usage_percent ?? 0
  const barColor =
    percent > 90 ? 'bg-rose-400' : percent > 70 ? 'bg-accent-400' : 'bg-primary-400'

  const tabs: UnderlineTabItem<Scope>[] = [
    { key: 'mine', label: t('me:storageMine'), badge: <TabCount n={mine.length} /> },
    { key: 'produced', label: t('me:storageProduced'), badge: <TabCount n={produced.length} /> },
    { key: 'viewed', label: t('me:storageViewed'), badge: <TabCount n={viewed.length} /> },
  ]

  return (
    <PageShell
      title={t('me:storage')}
      onBack={() => navigate('/me')}
      width="content"
      contentClassName="space-y-4"
    >
      {error && (
        <p className="text-sm text-rose-400 bg-rose-500/10 rounded-card px-4 py-3">{error}</p>
      )}

      <Card
        title={t('me:storage')}
        icon={<HardDrive size={16} className="text-primary-400" />}
        hint={t(HINT_KEY[scope])}
        action={
          scope === 'mine' ? (
            <Button size="sm" icon={<Upload size={14} />} onClick={() => fileInputRef.current?.click()}>
              {t('me:storageUpload')}
            </Button>
          ) : scope === 'produced' && storage ? (
            <span className="text-xs tabular-nums text-textMuted">
              {formatFileSize(storage.chat_used)}
            </span>
          ) : null
        }
      >
        <input ref={fileInputRef} type="file" className="hidden" onChange={handleUpload} />

        <UnderlineTabs items={tabs} value={scope} onChange={setScope} size="md" />

        {scope === 'mine' && (
          <>
            {storage && (
              <div className="space-y-2">
                <div className="flex items-baseline justify-between gap-3 text-xs">
                  <span className="text-textSecondary">
                    {t('me:used')}{' '}
                    <span className="font-medium text-textPrimary">{formatFileSize(storage.total_used)}</span>
                    <span className="text-textMuted">{' / '}{storage.quota_mb} MB</span>
                  </span>
                  <span className={`tabular-nums ${percent > 90 ? 'font-medium text-rose-400' : 'text-textMuted'}`}>
                    {percent}%
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-canvas">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${barColor}`}
                    style={{ width: `${Math.min(percent, 100)}%` }}
                  />
                </div>
                {percent > 90 && <p className="text-xs text-rose-400">{t('me:storageWarning')}</p>}
              </div>
            )}
            <FileList
              files={[...mine].sort(newestFirst)}
              renderRow={(f) => (
                <FileRow
                  file={f}
                  onOpen={() => setPreviewFile(f)}
                  trailing={
                    <button
                      type="button"
                      onClick={() => setForwardFile(f)}
                      title={t('forward:title')}
                      className="icon-btn-xs opacity-0 group-hover:opacity-100"
                    >
                      <Forward size={12} />
                    </button>
                  }
                />
              )}
            />
          </>
        )}

        {scope === 'produced' && (
          <FileList
            files={[...produced].sort(newestFirst)}
            renderRow={(f) => <FileRow file={f} onOpen={() => setPreviewFile(f)} />}
          />
        )}

        {scope === 'viewed' && (
          <FileList
            files={viewed}
            renderRow={(f) => (
              <FileRow
                file={f}
                unavailable={f.is_available === false}
                onOpen={f.is_available === false ? undefined : () => setPreviewFile(f)}
              />
            )}
          />
        )}
      </Card>

      {previewFile && (
        <FilePreviewModal
          fileId={previewFile.id}
          fileName={fileName(previewFile.path)}
          fileSize={previewFile.size}
          mimeType={previewFile.mime_type}
          onClose={() => setPreviewFile(null)}
        />
      )}

      {forwardFile && (
        <ForwardFileModal
          file={{
            file_id: forwardFile.id,
            name: fileName(forwardFile.path),
            size: forwardFile.size,
            mime_type: forwardFile.mime_type,
          }}
          onClose={() => setForwardFile(null)}
        />
      )}
    </PageShell>
  )
}

/** 页签右侧的条数角标：只做位置，配色留给这里 */
function TabCount({ n }: { n: number }) {
  return <span className="text-3xs tabular-nums text-textMuted">{n}</span>
}
