/**
 * i18n 字典加载器：前端所有 i18n 检查脚本的唯一字典来源。
 *
 * 为什么单独抽出来：check-i18n.mjs 原先用「正则截对象字面量 + new Function」解析 ns/*.ts，
 * check-i18n-keys.mjs 用 esbuild 转译后 import——同一份字典两套解析器，任何一边走偏
 * 就会出现「一个脚本绿、另一个红」的分裂，查起来还以为是文案问题。这里统一到 esbuild：
 * 它是仓库里已有的依赖，且真正执行 TS，不做文本猜测。
 *
 * 约定（新增命名空间不用改任何脚本）：
 *   src/i18n/ns/<ns>.ts 导出 <ns>Zh / <ns>En / <ns>Ja（common 相应为 commonZh / commonEn / commonJa）
 * 返回 { zh: { <ns>: { key: 文案 } }, en: {...}, ja: {...} }
 */
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import esbuild from 'esbuild'

export const LANGS = ['zh', 'en', 'ja']

const SUFFIX = { Zh: 'zh', En: 'en', Ja: 'ja' }

/** 把 ns/*.ts 就地转译后 import：不依赖跑着的 dev server，也不自己解析 TS */
export async function loadDicts(nsDir) {
  const dicts = { zh: {}, en: {}, ja: {} }
  for (const file of readdirSync(nsDir).filter((f) => f.endsWith('.ts'))) {
    const src = readFileSync(join(nsDir, file), 'utf8')
    const { code } = await esbuild.transform(src, { loader: 'ts', format: 'esm' })
    const mod = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))
    for (const [name, value] of Object.entries(mod)) {
      // 命名空间取导出名而不是文件名：translations.ts 登记的是导出名，两边对不上时以它为准
      const lang = SUFFIX[name.slice(-2)]
      if (lang) dicts[lang][name.slice(0, -2)] = value
    }
  }
  return dicts
}
