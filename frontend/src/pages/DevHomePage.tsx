import { useNavigate } from 'react-router-dom';
import { useLanguage } from '../i18n/useLanguage';
import SearchBar from '../components/SearchBar';
import type { Lang } from '../types';
import { getAppMode } from '../components/Navbar';

const DevHomePage = () => {
  const navigate = useNavigate();
  const { t } = useLanguage();

  const handleSearch = (query: string, lang: Lang) => {
    const mode = getAppMode();
    const params = new URLSearchParams();
    params.set('q', query);
    params.set('lang', lang);
    if (mode === 'dev') {
      navigate(`/dev/search?${params.toString()}`);
    } else {
      navigate(`/user/search?${params.toString()}`);
    }
  };

  return (
    <main className="flex-grow flex flex-col items-center px-4 sm:px-6 md:px-10 pt-16 sm:pt-24 pb-16 w-full max-w-container-max-width mx-auto">
      <div className="text-center max-w-3xl flex flex-col items-center gap-5 mb-10 page-enter">
        <span className="khatam" aria-hidden="true" />
        <h1 className="font-display-lg text-display-lg text-on-surface dark:text-dark-on-surface">
          {t('dev.homeTitle')}
        </h1>
        <p className="font-body-main text-body-main text-on-surface-variant dark:text-dark-on-surface-variant max-w-2xl mx-auto">
          {t('dev.homeSubtitle')}
        </p>
      </div>
      <div className="w-full max-w-3xl page-enter" style={{ animationDelay: '0.1s' }}>
        <SearchBar onSearch={handleSearch} />
      </div>
    </main>
  );
};

export default DevHomePage;
