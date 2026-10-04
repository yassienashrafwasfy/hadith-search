import { useLanguage } from '../i18n/useLanguage';

interface ErrorBannerProps {
  // A translation key (see api/errors.ts), never raw text.
  message: string | null;
  onDismiss?: () => void;
}

const ErrorBanner = ({ message, onDismiss }: ErrorBannerProps) => {
  const { t } = useLanguage();

  if (!message) return null;

  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 bg-error-container dark:bg-dark-error-container text-on-error-container dark:text-dark-on-error-container border-s-4 border-error dark:border-dark-error rounded px-4 py-3 animate-fade-in"
    >
      <div className="flex items-center gap-3">
        <span className="material-symbols-outlined text-[20px]" aria-hidden="true">error</span>
        <span className="font-ui-label text-ui-label">{t(message)}</span>
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={t('error.dismiss')}
          className="tap inline-flex items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
        >
          <span className="material-symbols-outlined text-[20px]" aria-hidden="true">close</span>
        </button>
      )}
    </div>
  );
};

export default ErrorBanner;
