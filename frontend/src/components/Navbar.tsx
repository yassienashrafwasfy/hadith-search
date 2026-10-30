import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ThemeToggle } from './ThemeToggle';
import { useLanguage } from '../i18n/useLanguage';

export const APP_MODE_KEY = 'app_mode';

export const getAppMode = (): string => localStorage.getItem(APP_MODE_KEY) || 'user';

export const setAppMode = (mode: string) => localStorage.setItem(APP_MODE_KEY, mode);

interface NavbarProps {
  mode: string;
  onModeChange: (mode: string) => void;
}

const Navbar = ({ mode, onModeChange }: NavbarProps) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { t } = useLanguage();
  const isDev = mode === 'dev';

  const navLinks = isDev
    ? [
        { labelKey: 'nav.search', path: '/dev/search' },
        { labelKey: 'nav.benchmarks', path: '/dev/benchmark' },
        { labelKey: 'nav.algorithmComparison', path: '/dev/compare' },
        { labelKey: 'nav.annotation', path: '/dev/annotation' },
        { labelKey: 'nav.kvPairs', path: '/dev/kv-pairs' },
      ]
    : [{ labelKey: 'nav.search', path: '/user/search' }];

  const handleModeToggle = () => {
    const path = location.pathname;
    const isSearch = path === '/user/search' || path === '/dev/search';

    if (isDev) {
      navigate(isSearch ? '/user/search' : '/user/');
    } else {
      navigate('/dev/search');
    }
    onModeChange(isDev ? 'user' : 'dev');
  };

  const linkClass = (active: boolean) =>
    `relative inline-flex items-center tap px-3 font-ui-label text-ui-label whitespace-nowrap transition-colors duration-200 ${
      active
        ? 'text-on-surface dark:text-dark-on-surface'
        : 'text-on-surface-variant dark:text-dark-on-surface-variant hover:text-on-surface dark:hover:text-dark-on-surface'
    }`;

  const links = navLinks.map((item) => {
    const active = location.pathname.startsWith(item.path);
    return (
      <Link key={item.path} to={item.path} aria-current={active ? 'page' : undefined} className={linkClass(active)}>
        {t(item.labelKey)}
        <span
          aria-hidden="true"
          className={`absolute inset-x-3 bottom-1 h-[2px] bg-secondary dark:bg-dark-secondary origin-right transition-transform duration-300 ${
            active ? 'scale-x-100' : 'scale-x-0'
          }`}
        />
      </Link>
    );
  });

  return (
    <header className="sticky top-0 z-40 bg-background dark:bg-dark-background double-rule">
      <div className="flex items-center justify-between gap-3 w-full px-4 sm:px-6 lg:px-10 py-2 max-w-[1280px] mx-auto">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            to={isDev ? '/dev/search' : '/user/'}
            className="flex items-center gap-2.5 tap text-on-surface dark:text-dark-on-surface"
          >
            <span className="khatam" aria-hidden="true" />
            <span className="font-display-lg text-[28px] leading-none pt-1" translate="no">{t('home.title')}</span>
          </Link>
          {isDev && (
            <span className="px-2 py-0.5 border border-secondary dark:border-dark-secondary text-secondary dark:text-dark-secondary font-ui-caption text-ui-caption">
              {t('mode.dev')}
            </span>
          )}
        </div>
        <nav aria-label={t('nav.main')} className="hidden md:flex items-center gap-1">
          {links}
        </nav>
        <div className="flex items-center gap-1 shrink-0">
          <ThemeToggle />
          <button
            type="button"
            onClick={handleModeToggle}
            aria-label={isDev ? t('mode.switchToUser') : t('mode.switchToDev')}
            className="inline-flex items-center gap-2 tap px-3 border border-outline-variant dark:border-dark-outline-variant text-on-surface-variant dark:text-dark-on-surface-variant hover:text-on-surface dark:hover:text-dark-on-surface hover:border-on-surface dark:hover:border-dark-on-surface transition-colors duration-200 font-ui-caption text-ui-caption rounded"
          >
            <span className="material-symbols-outlined text-[18px]" aria-hidden="true">
              {isDev ? 'person' : 'code'}
            </span>
            {isDev ? t('mode.user') : t('mode.dev')}
          </button>
        </div>
      </div>
      {isDev && (
        <nav
          aria-label={t('nav.main')}
          className="md:hidden flex overflow-x-auto scrollbar-hide px-2 border-t border-outline-variant dark:border-dark-outline-variant"
        >
          {links}
        </nav>
      )}
    </header>
  );
};

export default Navbar;
