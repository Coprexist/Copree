/**
 * MIME 类型判断与文件元信息工具（纯函数）。
 */

/** 可直接文本预览的 MIME 类型 */
export function isTextPreviewable(mimeType: string): boolean {
  if (mimeType.startsWith('text/')) return true
  const textish = [
    'application/json',
    'application/xml',
    'application/javascript',
    'application/x-yaml',
    'application/x-sh',
    'application/x-shellscript',
  ]
  return textish.includes(mimeType)
}

/** 扩展名 → 代码语言标签映射（用于语法高亮） */
export const EXT_LANG_MAP: Record<string, string> = {
  py: 'python', js: 'javascript', ts: 'typescript', jsx: 'jsx', tsx: 'tsx',
  c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
  json: 'json', xml: 'xml', html: 'html', css: 'css', scss: 'scss',
  yaml: 'yaml', yml: 'yaml', toml: 'toml',
  sh: 'bash', bash: 'bash', zsh: 'bash',
  sql: 'sql', rs: 'rust', go: 'go', java: 'java', kt: 'kotlin', swift: 'swift',
  php: 'php', rb: 'ruby', lua: 'lua', r: 'r', dart: 'dart',
}

/** 扩展名 → MIME 类型映射（后端缺失时回退） */
export const EXT_MIME_MAP: Record<string, string> = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
  webp: 'image/webp', svg: 'image/svg+xml', bmp: 'image/bmp',
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  json: 'application/json', xml: 'application/xml', yaml: 'application/x-yaml', yml: 'application/x-yaml',
  js: 'application/javascript', ts: 'text/typescript', py: 'text/x-python',
  c: 'text/x-c', cpp: 'text/x-c++src', h: 'text/x-c', hpp: 'text/x-c++src',
  sh: 'application/x-shellscript', bash: 'application/x-shellscript',
  md: 'text/markdown', txt: 'text/plain', html: 'text/html', css: 'text/css',
  csv: 'text/csv', log: 'text/plain', toml: 'application/toml', ini: 'text/plain',
  mp4: 'video/mp4', webm: 'video/webm', mp3: 'audio/mpeg', wav: 'audio/wav',
  zip: 'application/zip', tar: 'application/x-tar', gz: 'application/gzip',
}

/** 文件名 → 代码语言标签 */
export function getCodeLang(fileName: string, mimeType: string): string {
  const ext = fileName.split('.').pop()?.toLowerCase() || ''
  if (EXT_LANG_MAP[ext]) return EXT_LANG_MAP[ext]
  // MIME fallback
  if (mimeType.startsWith('text/') && mimeType !== 'text/plain' && mimeType !== 'text/markdown') {
    return mimeType.replace('text/x-', '').replace('text/', '')
  }
  return ''
}

/** 是否为 Markdown 文件 */
export function isMarkdownFile(fileName: string, mimeType: string): boolean {
  return mimeType === 'text/markdown' || fileName.endsWith('.md') || fileName.endsWith('.markdown')
}

/** 消息附件里的一张图：回复引用要把这条消息缩成小图，而不是只写"[图片]"三个字 */
export interface ImageAttachmentRef {
  file_id: number
  name?: string
}

/**
 * 从一条消息的附件里取第一张图（没有就 undefined）。
 *
 * 类型按 resolveMimeType 兜底：存量附件的 mime 可能是 application/octet-stream，
 * 只看 mime 会让老图在回复引用里退回"[图片]"。
 */
export function firstImageAttachment(
  attachments: Array<{ file_id?: number; name?: string; mime_type?: string }> | null | undefined,
): ImageAttachmentRef | undefined {
  const hit = (attachments || []).find(
    (a) => a?.file_id && resolveMimeType(a.name || '', a.mime_type || '').startsWith('image/'),
  )
  return hit ? { file_id: Number(hit.file_id), name: hit.name } : undefined
}

/**
 * 解析 MIME 类型：优先后端返回的 mimeType，缺失时从文件名扩展名推断。
 */
export function resolveMimeType(fileName: string, mimeType: string): string {
  if (mimeType && mimeType !== 'application/octet-stream') return mimeType
  const ext = fileName.split('.').pop()?.toLowerCase() || ''
  return EXT_MIME_MAP[ext] || 'application/octet-stream'
}
