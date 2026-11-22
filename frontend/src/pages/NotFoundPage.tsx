import { useNavigate } from 'react-router-dom'
import { useT } from '../i18n/I18nContext'
import { Button } from '../components/ui'

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
        <Button
          variant="primary"
          size="md"
          onClick={() => navigate('/')}
        >
          {t('notFound:backHome')}
        </Button>
      </div>
    </div>
  )
}
