import { useState, useCallback, useRef } from 'react'
import Cropper, { type Area } from 'react-easy-crop'
import { X, ZoomIn, ZoomOut } from 'lucide-react'
import { useT } from '../i18n/I18nContext'

interface AvatarCropModalProps {
  file: File
  onConfirm: (blob: Blob) => void
  onCancel: () => void
  cropShape?: 'round' | 'rect'
}

// 与后端 AVATAR_MAX_PX 保持一致（image_compress.py）
const AVATAR_MAX_PX = 4096

function getCroppedImgRaw(image: HTMLImageElement, crop: Area): Promise<Blob> {
  // 按原始像素坐标裁剪，超过上限降分辨率防浏览器崩溃
  const scale = Math.min(1, AVATAR_MAX_PX / Math.max(crop.width, crop.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(crop.width * scale)
  canvas.height = Math.round(crop.height * scale)
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('No 2d context')

  ctx.drawImage(
    image,
    crop.x,
    crop.y,
    crop.width,
    crop.height,
    0,
    0,
    canvas.width,
    canvas.height,
  )

  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob)
        else reject(new Error('Canvas toBlob failed'))
      },
      'image/png',
    )
  })
}

export default function AvatarCropModal({ file, onConfirm, onCancel, cropShape }: AvatarCropModalProps) {
  const t = useT()
  const [imageSrc, setImageSrc] = useState<string>('')
  const [crop, setCrop] = useState({ x: 0, y: 0 })
  const [zoom, setZoom] = useState(1)
  const croppedRef = useRef<Area | null>(null)

  // 加载图片
  useState(() => {
    const reader = new FileReader()
    reader.onload = () => {
      setImageSrc(reader.result as string)
    }
    reader.readAsDataURL(file)
  })

  const onCropComplete = useCallback((_: Area, croppedPixels: Area) => {
    croppedRef.current = croppedPixels
  }, [])

  const handleConfirm = useCallback(async () => {
    // GIF 动图跳过裁剪，直接传原文件保留动画
    if (file.type === 'image/gif') {
      onConfirm(file)
      return
    }
    const crop = croppedRef.current
    if (!imageSrc || !crop) {
      onConfirm(file)
      return
    }
    try {
      // 创建临时图片元素，crop 坐标已是原始图像像素坐标（react-easy-crop 自动处理缩放）
      const img = new Image()
      img.src = imageSrc
      await new Promise<void>((resolve, reject) => {
        img.onload = () => resolve()
        img.onerror = () => reject(new Error('Image load failed'))
      })
      const blob = await getCroppedImgRaw(img, crop)
      onConfirm(blob)
    } catch {
      onConfirm(file)
    }
  }, [file, imageSrc, onConfirm])

  if (!imageSrc) {
    return (
      <div className="fixed inset-0 z-modal flex items-center justify-center bg-black/70">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-modal flex flex-col bg-black/90">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between px-4 h-14 shrink-0">
        <button
          onClick={onCancel}
          className="icon-btn icon-btn-inverse rounded-full"
        >
          <X size={22} />
        </button>
        <span className="text-white/80 text-sm font-medium">{t('avatarCrop:adjustAvatar')}</span>
        <button
          onClick={handleConfirm}
          className="px-4 py-1.5 rounded-full bg-mint-400 hover:bg-mint-500 text-white text-sm font-medium transition-colors"
        >
          {t('common:confirm')}
        </button>
      </div>

      {/* 裁剪区域 */}
      <div className="flex-1 relative">
        <Cropper
          image={imageSrc}
          crop={crop}
          zoom={zoom}
          aspect={1}
          cropShape={cropShape || 'round'}
          classes={{
            cropAreaClassName: cropShape === 'rect' ? 'rounded-crop-area' : undefined,
          }}
          showGrid={false}
          onCropChange={setCrop}
          onCropComplete={onCropComplete}
          onZoomChange={setZoom}
        />
      </div>

      {/* 底部缩放控制 */}
      <div className="flex items-center justify-center gap-4 px-6 py-4 shrink-0">
        <ZoomOut size={18} className="text-white/60" />
        <input
          type="range"
          min={1}
          max={3}
          step={0.01}
          value={zoom}
          onChange={(e) => setZoom(Number(e.target.value))}
          className="w-full max-w-xs h-1.5 rounded-full appearance-none bg-white/30 accent-mint-400"
        />
        <ZoomIn size={18} className="text-white/60" />
      </div>
    </div>
  )
}
