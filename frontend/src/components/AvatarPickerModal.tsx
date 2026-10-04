import { useState, useEffect, useCallback, useRef } from 'react'
import { Image, Upload, X, Loader2 } from 'lucide-react'
import { api, fileDownloadUrl } from '../api/client'
import { useT } from '../i18n/I18nContext'
import AvatarCropModal from './AvatarCropModal'

interface FileItem {
  id: number
  path: string
  size: number
  mime_type: string
  created_at: string | null
}

interface AvatarPickerModalProps {
  /** 调用方传入的上传逻辑，接收裁剪后的 Blob */
  onUpload: (blob: Blob) => Promise<void>
  onClose: () => void
  /** 弹窗标题，默认 "设置头像" */
  title?: string
  /** 文件大小上限（MB），默认 10 */
  maxSizeMB?: number
  /** 裁剪形状，默认 round（圆形） */
  cropShape?: 'round' | 'rect'
}

export default function AvatarPickerModal({
  onUpload,
  onClose,
  title,
  maxSizeMB = 10,
  cropShape = 'round',
}: AvatarPickerModalProps) {
  const t = useT()
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 文件列表状态
  const [files, setFiles] = useState<FileItem[]>([])
  const [loadingFiles, setLoadingFiles] = useState(false)
  const [fileError, setFileError] = useState('')

  // 裁剪状态
  const [cropFile, setCropFile] = useState<File | null>(null)

  // 上传状态
  const [uploading, setUploading] = useState(false)

  // 第一步：选择模式
  const [step, setStep] = useState<'pick' | 'select-file'>('pick')

  // 加载个人空间文件
  const loadFiles = useCallback(async () => {
    setLoadingFiles(true)
    setFileError('')
    try {
      const data = await api.get<FileItem[]>('/fs/list?path=.')
      const list = Array.isArray(data) ? data : []
      // 只显示图片
      setFiles(list.filter((f) => f.mime_type?.startsWith('image/')))
    } catch (e: any) {
      setFileError(e?.detail || t('common:loadFailed'))
    } finally {
      setLoadingFiles(false)
    }
  }, [])

  // 每次切到 select-file 时加载
  useEffect(() => {
    if (step === 'select-file') {
      loadFiles()
    }
  }, [step, loadFiles])

  // 从文件选择器选择新图片，检测动图（GIF / WebP）跳过裁剪直接上传
  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    // 大小校验
    if (file.size > maxSizeMB * 1024 * 1024) {
      setFileError(t('error:avatarTooLarge'))
      e.target.value = ''
      return
    }
    // 读取前 12 字节检测动图（GIF / WebP），跳过裁剪
    file.slice(0, 12).arrayBuffer().then(buf => {
      const h = new Uint8Array(buf)
      const isGif = h[0] === 0x47 && h[1] === 0x49 && h[2] === 0x46
      const isWebP = h[0] === 0x52 && h[1] === 0x49 && h[2] === 0x46 && h[3] === 0x46
        && h[8] === 0x57 && h[9] === 0x45 && h[10] === 0x42 && h[11] === 0x50
      if (isGif || isWebP) {
        // 直接上传原文件
        setUploading(true)
        onUpload(file).then(onClose).catch((err: any) => {
          setFileError(err?.detail || err?.message || t('error:uploadFailed'))
          setUploading(false)
        })
        return
      }
      setCropFile(file)
    }).catch(() => {
      // fallback: 通过 MIME 判断
      if (file.type === 'image/gif') {
        setUploading(true)
        onUpload(file).then(onClose).catch((err: any) => {
          setFileError(err?.detail || err?.message || t('error:uploadFailed'))
          setUploading(false)
        })
        return
      }
      setCropFile(file)
    })
    e.target.value = ''
  }

  // 从个人空间选择图片
  const handleSelectFromSpace = async (fileItem: FileItem) => {
    try {
      const res = await fetch(fileDownloadUrl(fileItem.id))
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: t('common:loadFailed') }))
        throw new Error(err.detail || t('common:loadFailed'))
      }
      const blob = await res.blob()
      // 提取扩展名
      const ext = fileItem.path.includes('.') ? fileItem.path.split('.').pop() || 'jpg' : 'jpg'
      const file = new File([blob], fileItem.path.split('/').pop() || `image.${ext}`, {
        type: fileItem.mime_type || 'image/jpeg',
      })
      setCropFile(file)
    } catch (e: any) {
      setFileError(e?.message || t('common:loadFailed'))
    }
  }

  // 裁剪确认后调用 onUpload
  const handleCropConfirm = async (blob: Blob) => {
    setCropFile(null)
    setUploading(true)
    try {
      await onUpload(blob)
      onClose()
    } catch (e: any) {
      setFileError(e?.detail || e?.message || t('error:uploadFailed'))
      setUploading(false)
    }
  }

  const handleCropCancel = () => {
    setCropFile(null)
  }

  const modalTitle = title || t('groupSettings:avatarPickerTitle')

  return (
    <>
      {/* 遮罩 */}
      <div
        className="fixed inset-0 z-modal flex items-center justify-center bg-black/40"
        onClick={onClose}
      >
        <div
          className="relative bg-surface border border-border rounded-card shadow-2xl w-80 max-w-[90vw] p-5"
          onClick={(e) => e.stopPropagation()}
        >
          {/* 标题栏 */}
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-textPrimary">
              {modalTitle}
            </h3>
            <button
              onClick={onClose}
              className="icon-btn-sm text-textMuted"
            >
              <X size={16} />
            </button>
          </div>

          {/* 错误提示 */}
          {fileError && (
            <div className="mb-3 text-xs text-rose-400 bg-rose-400/10 rounded-control px-3 py-2">
              {fileError}
            </div>
          )}

          {/* 第一步：选择来源 */}
          {step === 'pick' && (
            <div className="grid grid-cols-2 gap-3">
              {/* 选项1：从个人空间选择 */}
              <button
                onClick={() => setStep('select-file')}
                className="flex flex-col items-center justify-center gap-2 p-4 rounded-card border border-border bg-elevated hover:bg-canvas transition-colors"
              >
                <div className="w-12 h-12 rounded-card bg-primary-500/10 dark:bg-primary-900/30 flex items-center justify-center">
                  <Image size={22} className="text-primary-400" />
                </div>
                <span className="text-sm font-medium text-textPrimary">
                  {t('groupSettings:avatarPickerFromSpace')}
                </span>
                <span className="text-3xs text-textMuted text-center leading-tight">
                  {t('groupSettings:avatarPickerFromSpaceDesc')}
                </span>
              </button>

              {/* 选项2：上传新图片 */}
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex flex-col items-center justify-center gap-2 p-4 rounded-card border border-border bg-elevated hover:bg-canvas transition-colors"
              >
                <div className="w-12 h-12 rounded-card bg-mint-400/10 dark:bg-mint-900/30 flex items-center justify-center">
                  <Upload size={22} className="text-mint-400" />
                </div>
                <span className="text-sm font-medium text-textPrimary">
                  {t('groupSettings:avatarPickerUploadNew')}
                </span>
                <span className="text-3xs text-textMuted text-center leading-tight">
                  {t('groupSettings:avatarPickerUploadNewDesc')}
                </span>
              </button>
            </div>
          )}

          {/* 第二步：从个人空间文件列表选择 */}
          {step === 'select-file' && (
            <div>
              {/* 返回按钮 */}
              <button
                onClick={() => setStep('pick')}
                className="mb-3 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors"
              >
                ← {t('common:back')}
              </button>

              {loadingFiles ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 size={20} className="animate-spin text-textMuted" />
                </div>
              ) : files.length === 0 ? (
                <div className="text-center py-6 text-textMuted">
                  <Image size={28} className="mx-auto mb-2 opacity-40" />
                  <p className="text-xs">{t('groupSettings:avatarPickerNoImages')}</p>
                </div>
              ) : (
                <div className="grid grid-cols-3 gap-2 max-h-64 overflow-y-auto">
                  {files.map((f) => (
                    <button
                      key={f.id}
                      onClick={() => handleSelectFromSpace(f)}
                      className="relative aspect-square rounded-control overflow-hidden border border-border bg-elevated hover:border-primary-400 transition-colors group"
                      title={f.path.split('/').pop() || f.path}
                    >
                      {/* 占位图垫在图片下方：加载失败只隐藏图片，不往 DOM 里塞节点 */}
                      <span className="absolute inset-0 flex items-center justify-center text-textMuted" aria-hidden="true">
                        <Image size={18} />
                      </span>
                      <img
                        src={fileDownloadUrl(f.id)}
                        alt={f.path}
                        className="relative w-full h-full object-cover"
                        loading="lazy"
                        onError={(e) => { e.currentTarget.style.display = 'none' }}
                      />
                      <div className="absolute inset-0 bg-primary-400/0 group-hover:bg-primary-400/10 transition-colors" />
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 隐藏的文件选择器 */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleFileInputChange}
          />

          {/* 上传指示 */}
          {uploading && (
            <div className="mt-3 flex items-center justify-center gap-2 text-xs text-primary-400">
              <Loader2 size={14} className="animate-spin" />
              {t('common:uploading')}
            </div>
          )}
        </div>
      </div>

      {/* 裁剪弹窗 */}
      {cropFile && (
        <AvatarCropModal
          file={cropFile}
          onConfirm={handleCropConfirm}
          onCancel={handleCropCancel}
          cropShape={cropShape}
        />
      )}
    </>
  )
}
