import { useT } from '../i18n/I18nContext'

/**
 * 登录页顶部的品牌区：图标 + 标题 + 副标题。
 *
 * 抽成独立组件是为了**单一来源**：登录页和进入平台的过场各渲染一份，
 * 过场那份按登录页量出来的位置铺上去，登录页卸载时视觉上完全接得住。
 * 两边各写一份标记的话，任何一次改字号/间距都会让过场里的那份错位。
 *
 * 外层的下边距（mb-10）由调用方给——登录页要它撑开与卡片的间距，
 * 过场里位置是绝对坐标，不需要。
 */
export default function LoginBrand() {
  const t = useT()
  return (
    <div className="text-center">
      <div className="relative inline-flex items-center justify-center mb-6">
        <div className="absolute w-20 h-20 rounded-full ai-pulse-active" />
        <div className="absolute w-16 h-16 rounded-full bg-primary-500/20 blur-xl" />
        <div className="relative w-14 h-14 rounded-dialog flex items-center justify-center shadow-lg shadow-primary-500/25 overflow-hidden">
          {/* 用矢量：Logo 在登录页是视觉中心，位图在高分屏/放大时一定糊（2026-10-27 用户反馈） */}
          <img src="/brand/coprexist-icon.svg" alt="Copree" className="w-full h-full object-contain" />
        </div>
      </div>
      {/* brand-title / brand-sub 只被进入平台的过场用到（紫幕上要换成白字），
          登录页里这两个 class 没有任何规则命中，配色仍是 tailwind 类 */}
      <h1 className="brand-title text-2xl font-bold text-textPrimary tracking-tight">{t('auth:title')}</h1>
      <p className="brand-sub text-sm text-textSecondary mt-2 font-medium">{t('auth:subtitle')}</p>
    </div>
  )
}
