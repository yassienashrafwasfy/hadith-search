import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import SearchBar from '../components/SearchBar';
import GradeFilter from '../components/GradeFilter';
import BookFilter from '../components/BookFilter';
import HadithOfTheDay from '../components/HadithOfTheDay';
import { POPULAR_QUERIES_AR } from '../constants';
import { useLanguage } from '../i18n/useLanguage';
import { getAppMode } from '../components/Navbar';
import type { Lang } from '../types';

const HOTD_ID_KEY = 'hotd_id';

const UserHomePage = () => {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const [selectedGrade, setSelectedGrade] = useState<string | null>(null);
  const [selectedBook, setSelectedBook] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionStorage.getItem(HOTD_ID_KEY)) {
      const randomId = Math.floor(Math.random() * 33738) + 1;
      sessionStorage.setItem(HOTD_ID_KEY, String(randomId));
    }
  }, []);

  const handleSearch = (query: string, lang: Lang) => {
    const mode = getAppMode();
    const params = new URLSearchParams();
    params.set('q', query);
    params.set('lang', lang);
    if (selectedGrade) params.set('grade', selectedGrade);
    if (selectedBook) params.set('book', selectedBook);
    if (mode === 'dev') {
      navigate(`/dev/search?${params.toString()}`);
    } else {
      navigate(`/user/search?${params.toString()}`);
    }
  };

  return (
    <main className="flex-grow flex flex-col items-center px-4 sm:px-6 md:px-10 pt-12 sm:pt-20 pb-16 w-full max-w-container-max-width mx-auto">
      <div className="text-center max-w-3xl flex flex-col items-center gap-5 mb-10 page-enter">
        <span className="khatam" aria-hidden="true" />
        <h1 className="font-display-lg text-display-lg text-on-surface dark:text-dark-on-surface">
          {t('home.title')}
        </h1>
        <p className="font-body-main text-body-main text-on-surface-variant dark:text-dark-on-surface-variant max-w-2xl mx-auto">
          {t('user.subtitle')}
        </p>
      </div>

      <div className="w-full max-w-3xl flex flex-col gap-6 page-enter" style={{ animationDelay: '0.1s' }}>
        <SearchBar onSearch={handleSearch} />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <GradeFilter value={selectedGrade} onChange={setSelectedGrade} />
          <BookFilter value={selectedBook} onChange={setSelectedBook} />
        </div>

        <section aria-labelledby="topics-h" className="flex flex-col gap-3">
          <h2 id="topics-h" className="font-ui-label text-ui-label text-on-surface-variant dark:text-dark-on-surface-variant">
            {t('home.topics')}
          </h2>
          <ul className="flex flex-wrap gap-2">
            {POPULAR_QUERIES_AR.map((tag) => (
              <li key={tag}>
                <button
                  type="button"
                  onClick={() => handleSearch(tag, 'ar')}
                  className="tap px-4 rounded border border-outline-variant dark:border-dark-outline-variant text-on-surface dark:text-dark-on-surface font-ui-caption text-ui-caption hover:border-secondary dark:hover:border-dark-secondary hover:text-secondary dark:hover:text-dark-secondary transition-colors"
                >
                  {tag}
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="w-full mt-16 sm:mt-20">
        <HadithOfTheDay />
      </div>
    </main>
  );
};

export default UserHomePage;
