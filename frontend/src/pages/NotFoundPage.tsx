import { useNavigate } from 'react-router-dom'
import { useT } from '../i18n/I18nContext'

export default function NotFoundPage() {
  const navigate = useNavigate()
  const t = useT()

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <div className="text-center px-6">
        <div className="text-7xl font-bold text-textMuted/30 mb-4">404</div>
        <h1 className="text-xl font-semibold text-textPrimary mb-2">{t('notFound:title')}</h1>
        <p className="text-sm text-textMuted mb-6 max-w-xs mx-auto leading-relaxed">
          {t('notFound:desc')}
        </p>
        <button
          onClick={() => navigate('/')}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-card bg-primary-500 text-white hover:bg-primary-600 transition-colors text-sm font-medium"
        >
          {t('notFound:backHome')}
        </button>
      </div>
    </div>
  )
}
