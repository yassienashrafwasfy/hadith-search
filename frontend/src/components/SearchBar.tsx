import { useState } from 'react';
import { useLanguage } from '../i18n/useLanguage';
import type { Lang } from '../types';

interface SearchBarProps {
  onSearch: (query: string, lang: Lang) => void;
  placeholder?: string;
  compact?: boolean;
  initialQuery?: string;
  disabled?: boolean;
}

// The site searches Arabic text only, so every query goes out as Arabic.
const SEARCH_LANG: Lang = 'ar';

const SearchBar = ({ onSearch, placeholder, compact = false, initialQuery = '', disabled = false }: SearchBarProps) => {
  const { t } = useLanguage();
  const [query, setQuery] = useState(initialQuery);

  const handleSubmit = () => {
    if (query.trim()) {
      onSearch(query.trim(), SEARCH_LANG);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSubmit();
    }
  };

  return (
    <div className="w-full">
      <div className="flex items-center gap-3">
        <div className="relative w-full group">
          <div className="absolute inset-y-0 start-6 flex items-center pointer-events-none">
            <span className="material-symbols-outlined text-secondary dark:text-dark-secondary fill group-focus-within:text-primary dark:group-focus-within:text-dark-primary transition-colors duration-300">
              search
            </span>
          </div>
          <input
            className={`w-full ${compact ? 'h-12 ps-14' : 'h-16 ps-16'} rounded-full border-2 border-outline-variant dark:border-dark-outline-variant bg-surface-container-lowest dark:bg-dark-surface-container-lowest text-on-surface dark:text-dark-on-surface font-ui-label text-ui-label focus:border-secondary dark:focus:border-dark-secondary focus:ring-4 focus:ring-secondary-container/30 dark:focus:ring-dark-secondary-container/30 transition-all shadow-sm placeholder:text-outline dark:placeholder:text-dark-outline`}
            placeholder={placeholder || t('search.placeholder')}
            type="text"
            lang="ar"
            dir="rtl"
            aria-label={t('search.placeholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={disabled || !query.trim()}
          className={`shrink-0 ${compact ? 'h-12 px-4' : 'h-16 px-6'} bg-primary dark:bg-dark-primary text-on-primary dark:text-dark-on-primary rounded-full font-ui-label text-ui-label hover:bg-primary-container dark:hover:bg-dark-primary-container transition-all duration-200 flex items-center gap-2 hover:shadow-md active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed`}
        >
          <span className="material-symbols-outlined text-[18px]">search</span>
          {!compact && t('search.button')}
        </button>
      </div>
    </div>
  );
};

export default SearchBar;
