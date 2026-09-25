import type { ElementType } from 'react'
import { Box, Globe, Palette, Plug, Server, Wand2 } from 'lucide-react'

/**
 * 插件类别的单一来源：图标 + i18n key。
 *
 * 为什么单独放一个文件：管理页的能力面板、总商城的插件分区都要画"类型"，
 * 各写一份映射必然漂移（以前就是硬编码中文，日文界面下也显示中文）。
 */
export const PLUGIN_CATEGORIES = ['skin', 'skill', 'world', 'service', 'other'] as const

export const CATEGORY_ICON: Record<string, ElementType> = {
  skin: Palette,
  skill: Wand2,
  world: Globe,
  service: Server,
  builtin: Plug,
  other: Box,
}

export const CATEGORY_LABEL_KEY: Record<string, string> = {
  skin: 'tool:store.catSkin',
  skill: 'tool:store.catSkill',
  world: 'tool:store.catWorld',
  service: 'tool:store.catService',
  builtin: 'tool:store.catBuiltin',
  other: 'tool:store.catOther',
}
