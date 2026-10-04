/**
 * i18n 字典类型：translations.ts 注册表与 ns/*.ts 各命名空间文件共用。
 *
 * 单独成文件是为了让命名空间文件不必反向 import 注册表，避免循环依赖。
 */

/** 单语言、单命名空间的扁平字典 */
export type TranslationDict = Record<string, string | Record<string, unknown>>

/** 命名空间字典：ns → 扁平 key 字典（每个分区一个） */
export type NamespacedDict = Record<string, TranslationDict>
