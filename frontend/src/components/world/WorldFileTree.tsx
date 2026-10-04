/**
 * 世界文件树：按目录层级构建 + 文件夹可折叠 + 文件选中/删除
 * （从 WorldDesignPage 拆分；移动端目录导航复用 buildWorldTree）
 *
 * 效率约定：整棵树 memo（父级因页签/对话重渲时，文件不变就不重进）；
 * 文件行单独 memo 且只吃原始值，选中文件时只有"旧选中 + 新选中"两行重渲。
 */
import { memo, useMemo } from 'react'
import { ChevronRight, Folder, FolderOpen, Trash2 } from 'lucide-react'
import { fileTypeIcon } from './FileContentPane'
import { useT } from '../../i18n/I18nContext'

export interface WorldFile {
  path: string
  size: number
}

export interface WorldTreeNode {
  name: string
  path: string
  children: WorldTreeNode[]
  isDir: boolean
}

/** 扁平文件列表 → 目录树（文件夹在前、按名排序） */
export function buildWorldTree(files: WorldFile[]): WorldTreeNode {
  const root: WorldTreeNode = { name: '', path: '', children: [], isDir: true }
  for (const f of files) {
    const parts = f.path.split('/')
    let node = root
    let acc = ''
    for (let i = 0; i < parts.length; i++) {
      acc = acc ? `${acc}/${parts[i]}` : parts[i]
      const isLast = i === parts.length - 1
      let child = node.children.find((c) => c.name === parts[i] && c.isDir === !isLast)
      if (!child) {
        child = { name: parts[i], path: acc, children: [], isDir: !isLast }
        node.children.push(child)
      }
      node = child
    }
  }
  const sortNodes = (nodes: WorldTreeNode[]) => {
    nodes.sort((a, b) => (a.isDir === b.isDir ? a.name.localeCompare(b.name) : a.isDir ? -1 : 1))
    nodes.forEach((n) => sortNodes(n.children))
  }
  sortNodes(root.children)
  return root
}

/** 层级引导线：每级 7px + 1px 竖线，层级越深竖线越多。
 *  原来每级 14px 纯缩进，左栏拖窄之后三四级就只能靠猜；竖线是 IDE 的通用做法，
 *  用一半的宽度就能读出层级，深文件也不会把文件名挤没。
 *  行是 items-stretch 且行内不留纵向 padding（padding 落在内容块上），竖线才能上下接住不出现断点。 */
function IndentGuides({ depth }: { depth: number }) {
  if (depth <= 0) return null
  return (
    <span className="flex self-stretch shrink-0" aria-hidden>
      {Array.from({ length: depth }, (_, i) => (
        <span key={i} className="w-[7px] flex justify-center">
          <span className="w-px self-stretch bg-border/60" />
        </span>
      ))}
    </span>
  )
}

/** 行内容块：图标与文字统一在这里，纵向 padding 只写这一处 */
const ROW_INNER = 'flex items-center gap-1 min-w-0 flex-1 py-1 pl-1'

interface TreeFileRowProps {
  path: string
  name: string
  depth: number
  active: boolean
  onSelect: (path: string) => void
  onDelete: (path: string) => void
}

/** 文件行：只吃原始值 + 稳定回调，memo 才有意义（key 也是稳定 path，不用下标） */
const TreeFileRow = memo(function TreeFileRow({ path, name, depth, active, onSelect, onDelete }: TreeFileRowProps) {
  const t = useT()
  return (
    <div className="group flex items-stretch">
      <button
        onClick={() => onSelect(path)}
        className={`flex items-stretch flex-1 min-w-0 text-left text-xs rounded transition-colors ${active ? 'bg-primary-500/20 text-primary-300' : 'hover:bg-elevated text-textSecondary'}`}
        title={path}
      >
        <IndentGuides depth={depth} />
        <span className={`${ROW_INNER} pr-1`}>
          {/* 12px 占位 = 文件夹行展开箭头的宽度，同级文件名才能与文件夹名对齐 */}
          <span className="w-3 shrink-0" aria-hidden />
          <span className="shrink-0">{fileTypeIcon(name)}</span>
          <span className="truncate">{name}</span>
        </span>
      </button>
      <button
        onClick={(ev) => { ev.stopPropagation(); onDelete(path) }}
        className="hidden group-hover:flex shrink-0 items-center justify-center w-6 text-textMuted hover:text-rose-400 transition-colors"
        title={t('tool:world.editor.deleteFile')}
      >
        <Trash2 size={13} />
      </button>
    </div>
  )
})

interface WorldFileTreeProps {
  files: WorldFile[]
  currentFile: string
  collapsedDirs: Set<string>
  onToggleDir: (path: string) => void
  onSelect: (path: string) => void
  onDelete: (path: string) => void
}

const WorldFileTree = memo(function WorldFileTree({ files, currentFile, collapsedDirs, onToggleDir, onSelect, onDelete }: WorldFileTreeProps) {
  // 建树是 O(n log n) 且每次都新建对象：按 files 定住，别让父级重渲把它一起带跑
  const tree = useMemo(() => buildWorldTree(files), [files])

  const renderTree = (nodes: WorldTreeNode[], depth: number): React.ReactElement[] =>
    nodes.map((n) => (
      <div key={n.path}>
        {n.isDir ? (
          <>
            <button
              onClick={() => onToggleDir(n.path)}
              className="flex items-stretch w-full text-left text-xs rounded transition-colors hover:bg-elevated text-textSecondary"
              title={n.path}
            >
              <IndentGuides depth={depth} />
              <span className={`${ROW_INNER} pr-2`}>
                <ChevronRight size={12} className={`shrink-0 transition-transform ${collapsedDirs.has(n.path) ? '' : 'rotate-90'}`} />
                {collapsedDirs.has(n.path) ? <Folder size={13} className="text-textMuted shrink-0" /> : <FolderOpen size={13} className="text-primary-400 shrink-0" />}
                <span className="truncate">{n.name}</span>
              </span>
            </button>
            {!collapsedDirs.has(n.path) && renderTree(n.children, depth + 1)}
          </>
        ) : (
          <TreeFileRow
            key={n.path}
            path={n.path}
            name={n.name}
            depth={depth}
            active={currentFile === n.path}
            onSelect={onSelect}
            onDelete={onDelete}
          />
        )}
      </div>
    ))

  return <>{renderTree(tree.children, 0)}</>
})

export default WorldFileTree
