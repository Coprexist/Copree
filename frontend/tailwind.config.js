/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
        mono: ['JetBrains Mono', 'SF Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      colors: {
        // 深邃紫金 — Copree 品牌色（深浅主题共用）
        // 结构色通过 CSS 变量切换，支持浅色/深色双主题
        canvas: 'rgb(var(--tw-canvas) / <alpha-value>)',
        surface: 'rgb(var(--tw-surface) / <alpha-value>)',
        elevated: 'rgb(var(--tw-elevated) / <alpha-value>)',
        border: 'rgb(var(--tw-border) / <alpha-value>)',
        // 文本色
        textPrimary: 'rgb(var(--tw-text-primary) / <alpha-value>)',
        textSecondary: 'rgb(var(--tw-text-secondary) / <alpha-value>)',
        textMuted: 'rgb(var(--tw-text-muted) / <alpha-value>)',
        // 自己的消息气泡（日夜两套主题变量）
        bubble: 'rgb(var(--tw-bubble) / <alpha-value>)',
        bubbleInk: 'rgb(var(--tw-bubble-ink) / <alpha-value>)',
        // 主题色 —— CSS 变量驱动（index.css :root/.dark 各配一套）
        // 浅色主题整体深一档（对比度达标，不发虚）；深色主题保持亮紫发光感
        primary: {
          50:  'rgb(var(--tw-primary-50) / <alpha-value>)',
          100: 'rgb(var(--tw-primary-100) / <alpha-value>)',
          200: 'rgb(var(--tw-primary-200) / <alpha-value>)',
          300: 'rgb(var(--tw-primary-300) / <alpha-value>)',
          400: 'rgb(var(--tw-primary-400) / <alpha-value>)',   // 强调文字/图标
          500: 'rgb(var(--tw-primary-500) / <alpha-value>)',   // 按钮主色
          600: 'rgb(var(--tw-primary-600) / <alpha-value>)',   // hover 加深
          700: 'rgb(var(--tw-primary-700) / <alpha-value>)',
          800: 'rgb(var(--tw-primary-800) / <alpha-value>)',
          900: 'rgb(var(--tw-primary-900) / <alpha-value>)',
        },
        accent: {
          50:  'rgb(var(--tw-accent-50) / <alpha-value>)',
          100: 'rgb(var(--tw-accent-100) / <alpha-value>)',
          200: 'rgb(var(--tw-accent-200) / <alpha-value>)',
          300: 'rgb(var(--tw-accent-300) / <alpha-value>)',
          400: 'rgb(var(--tw-accent-400) / <alpha-value>)',   // 琥珀金（状态变更/通知）
          500: 'rgb(var(--tw-accent-500) / <alpha-value>)',
          600: 'rgb(var(--tw-accent-600) / <alpha-value>)',
          700: 'rgb(var(--tw-accent-700) / <alpha-value>)',
        },
        mint: {
          50:  'rgb(var(--tw-mint-50) / <alpha-value>)',
          100: 'rgb(var(--tw-mint-100) / <alpha-value>)',
          200: 'rgb(var(--tw-mint-200) / <alpha-value>)',
          300: 'rgb(var(--tw-mint-300) / <alpha-value>)',
          400: 'rgb(var(--tw-mint-400) / <alpha-value>)',     // 在线/活跃绿
          500: 'rgb(var(--tw-mint-500) / <alpha-value>)',
          600: 'rgb(var(--tw-mint-600) / <alpha-value>)',     // hover 加深
          700: 'rgb(var(--tw-mint-700) / <alpha-value>)',
          800: 'rgb(var(--tw-mint-800) / <alpha-value>)',
          900: 'rgb(var(--tw-mint-900) / <alpha-value>)',
        },
        azure: {
          50:  'rgb(var(--tw-azure-50) / <alpha-value>)',
          100: 'rgb(var(--tw-azure-100) / <alpha-value>)',
          200: 'rgb(var(--tw-azure-200) / <alpha-value>)',
          300: 'rgb(var(--tw-azure-300) / <alpha-value>)',
          400: 'rgb(var(--tw-azure-400) / <alpha-value>)',     // 冷色文字/图标
          500: 'rgb(var(--tw-azure-500) / <alpha-value>)',     // 色条/实底
          600: 'rgb(var(--tw-azure-600) / <alpha-value>)',
          700: 'rgb(var(--tw-azure-700) / <alpha-value>)',
        },
        cyan: {
          50:  'rgb(var(--tw-cyan-50) / <alpha-value>)',
          100: 'rgb(var(--tw-cyan-100) / <alpha-value>)',
          200: 'rgb(var(--tw-cyan-200) / <alpha-value>)',
          300: 'rgb(var(--tw-cyan-300) / <alpha-value>)',
          400: 'rgb(var(--tw-cyan-400) / <alpha-value>)',     // 柔青（备用）
          500: 'rgb(var(--tw-cyan-500) / <alpha-value>)',
          600: 'rgb(var(--tw-cyan-600) / <alpha-value>)',
          700: 'rgb(var(--tw-cyan-700) / <alpha-value>)',
        },
        rose: {
          50:  'rgb(var(--tw-rose-50) / <alpha-value>)',
          100: 'rgb(var(--tw-rose-100) / <alpha-value>)',
          200: 'rgb(var(--tw-rose-200) / <alpha-value>)',
          300: 'rgb(var(--tw-rose-300) / <alpha-value>)',
          400: 'rgb(var(--tw-rose-400) / <alpha-value>)',     // 勿扰/危险文字
          500: 'rgb(var(--tw-rose-500) / <alpha-value>)',     // 危险按钮
          600: 'rgb(var(--tw-rose-600) / <alpha-value>)',     // hover 加深
          700: 'rgb(var(--tw-rose-700) / <alpha-value>)',
          800: 'rgb(var(--tw-rose-800) / <alpha-value>)',
          900: 'rgb(var(--tw-rose-900) / <alpha-value>)',
        },
      },
      /* ── 尺度令牌（全站单一来源，新增代码只用令牌名，不写裸数值）─────
         圆角：control=8px 控件/按钮 · card=12px 卡片/面板 · dialog=16px 弹窗/抽屉
               胶囊与圆头像用 rounded-full，其余裸值不再使用。 */
      borderRadius: {
        control: '0.5rem',
        card: '0.75rem',
        dialog: '1rem',
      },
      /* 字号：3xs=10px 极小徽标 · 2xs=11px 次级说明 · xs=12px 正文辅助
              sm=14px 正文 · base=16px 强调 …（其余沿用 Tailwind 默认） */
      fontSize: {
        '3xs': ['0.625rem', { lineHeight: '0.875rem' }],
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      /* 层级：只允许这五档，禁止再写裸 z-[n] */
      zIndex: {
        overlay: '30',   // 页面内局部遮罩 / 拖拽把手
        drawer: '40',    // 侧边抽屉 / 移动端导航
        modal: '50',     // 弹窗
        toast: '60',     // 全局通知 / 嵌套弹窗 / 维护横幅
        max: '100',      // 阻断式提示（余额不足等）
      },
      animation: {
        'pulse-ring': 'pulseRing 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'shimmer': 'shimmer 2.5s ease-in-out infinite',
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
        'pop-in': 'popIn 0.18s ease-out',
      },
      keyframes: {
        pulseRing: {
          '0%':   { boxShadow: '0 0 0 0 rgba(167, 139, 250, 0.4)' },
          '70%':  { boxShadow: '0 0 0 8px rgba(167, 139, 250, 0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(167, 139, 250, 0)' },
        },
        shimmer: {
          '0%, 100%': { opacity: '0.3' },
          '50%':      { opacity: '1' },
        },
        fadeIn: {
          '0%':   { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideUp: {
          '0%':   { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        popIn: {
          '0%':   { opacity: '0', transform: 'translateY(-6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
