import { useTheme } from '../hooks/useTheme';
import { useLanguage } from '../i18n/useLanguage';

export const ThemeToggle = () => {
  const { isDark, toggleTheme } = useTheme();
  const { t } = useLanguage();
  const label = isDark ? t('theme.switchToLight') : t('theme.switchToDark');

  return (
    <button
      type="button"
      onClick={toggleTheme}
      className="tap inline-flex items-center justify-center rounded text-on-surface dark:text-dark-on-surface hover:bg-surface-container dark:hover:bg-dark-surface-container transition-colors duration-200"
      aria-label={label}
      title={label}
    >
      <span className="material-symbols-outlined" aria-hidden="true">
        {isDark ? 'light_mode' : 'dark_mode'}
      </span>
    </button>
  );
};
