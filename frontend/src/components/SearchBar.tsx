import { useRef, useState, type FormEvent } from 'react';
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

// One line of writing: a ruled underline that fills with rubric ink while the field has focus.
const SearchBar = ({ onSearch, placeholder, compact = false, initialQuery = '', disabled = false }: SearchBarProps) => {
  const { t } = useLanguage();
  const [query, setQuery] = useState(initialQuery);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (disabled) return;
    if (query.trim()) {
      onSearch(query.trim(), SEARCH_LANG);
    } else {
      inputRef.current?.focus();
    }
  };

  return (
    <form role="search" onSubmit={handleSubmit} className="w-full">
      <label htmlFor={compact ? 'search-q-compact' : 'search-q'} className="sr-only">
        {t('search.label')}
      </label>
      <div className="flex items-stretch gap-3 sm:gap-5">
        <div className="ink-field flex-1 min-w-0 flex items-center gap-3 border-b-2 border-on-surface dark:border-dark-on-surface">
          <span
            aria-hidden="true"
            className={`material-symbols-outlined text-on-surface-variant dark:text-dark-on-surface-variant ${compact ? 'text-[22px]' : 'text-[28px]'}`}
          >
            search
          </span>
          <input
            ref={inputRef}
            id={compact ? 'search-q-compact' : 'search-q'}
            name="q"
            type="search"
            enterKeyHint="search"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            lang="ar"
            dir="rtl"
            className={`flex-1 min-w-0 bg-transparent border-0 outline-none focus-visible:outline-none font-body-arabic text-on-surface dark:text-dark-on-surface placeholder:text-on-surface-variant/70 dark:placeholder:text-dark-on-surface-variant/70 ${
              compact ? 'text-[22px] py-2' : 'text-[26px] sm:text-[30px] py-3'
            }`}
            placeholder={placeholder || t('search.placeholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <button
          type="submit"
          disabled={disabled}
          className="shrink-0 tap px-5 sm:px-7 bg-primary dark:bg-dark-primary text-on-primary dark:text-dark-on-primary hover:bg-primary-container dark:hover:bg-dark-primary-container active:translate-y-px transition-[background-color,transform] duration-200 font-ui-label text-ui-label rounded disabled:opacity-60 disabled:cursor-wait"
        >
          {t('search.button')}
        </button>
      </div>
    </form>
  );
};

export default SearchBar;
