/**
 * 世界文件内容区：md/html/代码渲染、图片显示、纯文本编辑
 * （桌面右栏 / 移动端编辑器共用；从 WorldDesignPage 拆分）
 */
import { memo } from 'react'
import { FileText, FileCode, FileJson, FileImage, FileAudio, FileVideo, File } from 'lucide-react'
import CodeRenderer from '../shared/CodeRenderer'
import MarkdownContent from '../shared/MarkdownContent'
import { useT } from '../../i18n/I18nContext'

// 文件类型图标（与主界面风格一致）
export function fileTypeIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp'].includes(ext)) return <FileImage size={13} className="text-mint-400 shrink-0" />
  if (['mp3', 'wav', 'ogg'].includes(ext)) return <FileAudio size={13} className="text-primary-400 shrink-0" />
  if (['mp4', 'webm'].includes(ext)) return <FileVideo size={13} className="text-rose-400 shrink-0" />
  if (['json'].includes(ext)) return <FileJson size={13} className="text-accent-400 shrink-0" />
  if (['md', 'txt'].includes(ext)) return <FileText size={13} className="text-textSecondary shrink-0" />
  if (['html', 'htm', 'css', 'js', 'ts', 'jsx', 'tsx', 'py', 'xml', 'yaml', 'yml', 'sh'].includes(ext)) return <FileCode size={13} className="text-primary-400 shrink-0" />
  return <File size={13} className="text-textMuted shrink-0" />
}

// 文件内容区：md/html/代码渲染、图片显示、纯文本编辑（桌面右栏 / 移动端编辑器共用）
// memo：props 全是原始值 + 稳定的 setState，左栏切页签/对话流式输出都不该让编辑器重渲一遍
function FileContentPane({ wid, currentFile, content, setContent, viewMode, canRender, isMdFile, fileCodeLang, isImgFile }: {
  wid: number
  currentFile: string
  content: string
  setContent: (v: string) => void
  viewMode: 'edit' | 'render'
  canRender: boolean
  isMdFile: boolean
  fileCodeLang: string
  isImgFile: boolean
}) {
  const t = useT()
  if (viewMode === 'render' && canRender) {
    if (isImgFile) {
      return (
        <div className="flex-1 overflow-hidden bg-canvas flex items-center justify-center">
          <img
            src={`/world/${wid}/files/${currentFile.split('/').map(encodeURIComponent).join('/')}`}
            alt={currentFile}
            className="w-full h-full object-contain"
          />
        </div>
      )
    }
    return (
      <div className="flex-1 overflow-auto bg-canvas [&_code]:!overflow-x-visible [&_code]:!rounded-none [&_code]:!border-0 [&_code]:!p-0 [&_code]:!bg-transparent">
        <div className="w-full max-w-none text-sm leading-relaxed break-words text-textPrimary p-3 md:p-4">
          {isMdFile ? (
            <MarkdownContent content={content} isMine={false} />
          ) : (
            <CodeRenderer className={'language-' + fileCodeLang}>{content}</CodeRenderer>
          )}
        </div>
      </div>
    )
  }
  return (
    <textarea
      value={content}
      onChange={(e) => setContent(e.target.value)}
      spellCheck={false}
      className="flex-1 bg-canvas text-sm text-textPrimary p-3 font-mono outline-none resize-none"
      placeholder={t('tool:world.editor.codePlaceholder')}
    />
  )
}

export default memo(FileContentPane)
