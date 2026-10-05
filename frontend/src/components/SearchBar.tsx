import { useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useLanguage } from '../i18n/useLanguage';
import { useSuggestions } from '../hooks/useSuggestions';
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
// While the user types, a list of suggestions (an ARIA combobox) opens under the line.
const SearchBar = ({ onSearch, placeholder, compact = false, initialQuery = '', disabled = false }: SearchBarProps) => {
  const { t } = useLanguage();
  const [query, setQuery] = useState(initialQuery);
  const [previousInitial, setPreviousInitial] = useState(initialQuery);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const inputId = compact ? 'search-q-compact' : 'search-q';

  // The page can set a new query (a clicked "did you mean"); show it in the field.
  if (initialQuery !== previousInitial) {
    setPreviousInitial(initialQuery);
    setQuery(initialQuery);
  }

  const suggestions = useSuggestions(query, open && !disabled);
  const expanded = open && suggestions.length > 0;
  const active = activeIndex < suggestions.length ? activeIndex : -1;
  const optionId = (index: number) => `${listId}-option-${index}`;

  const submit = (text: string) => {
    setOpen(false);
    setActiveIndex(-1);
    if (disabled) return;
    if (text.trim()) {
      onSearch(text.trim(), SEARCH_LANG);
    } else {
      inputRef.current?.focus();
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(query);
  };

  const choose = (index: number) => {
    const text = suggestions[index].text;
    setQuery(text);
    submit(text);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setOpen(true);
        setActiveIndex(expanded ? (active + 1) % suggestions.length : -1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (expanded) setActiveIndex(active <= 0 ? suggestions.length - 1 : active - 1);
        break;
      case 'Enter':
        if (expanded && active >= 0) {
          e.preventDefault();
          choose(active);
        }
        break;
      case 'Escape':
        if (expanded) {
          e.preventDefault();
          setOpen(false);
          setActiveIndex(-1);
        }
        break;
    }
  };

  return (
    <form role="search" onSubmit={handleSubmit} className="w-full">
      <label htmlFor={inputId} className="sr-only">
        {t('search.label')}
      </label>
      <div className="flex items-stretch gap-3 sm:gap-5">
        <div className="relative flex-1 min-w-0">
          <div className="ink-field flex items-center gap-3 border-b-2 border-on-surface dark:border-dark-on-surface">
            <span
              aria-hidden="true"
              className={`material-symbols-outlined text-on-surface-variant dark:text-dark-on-surface-variant ${compact ? 'text-[22px]' : 'text-[28px]'}`}
            >
              search
            </span>
            <input
              ref={inputRef}
              id={inputId}
              name="q"
              type="search"
              role="combobox"
              aria-autocomplete="list"
              aria-haspopup="listbox"
              aria-expanded={expanded}
              aria-controls={listId}
              aria-activedescendant={expanded && active >= 0 ? optionId(active) : undefined}
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
              onChange={(e) => {
                setQuery(e.target.value);
                setOpen(true);
                setActiveIndex(-1);
              }}
              onKeyDown={handleKeyDown}
              onBlur={() => setOpen(false)}
            />
          </div>

          <ul
            id={listId}
            role="listbox"
            aria-label={t('suggestions.label')}
            hidden={!expanded}
            dir="rtl"
            className="absolute inset-x-0 top-full z-30 mt-1 max-h-[22rem] overflow-y-auto bg-surface-bright dark:bg-dark-surface-bright border border-outline-variant dark:border-dark-outline-variant rounded shadow-[0_8px_24px_-12px_rgba(29,34,48,0.35)] py-1"
          >
            {suggestions.map((item, index) => (
              <li
                key={`${item.kind}-${item.text}`}
                id={optionId(index)}
                role="option"
                aria-selected={index === active}
                // mousedown (not click) so the input keeps focus and onBlur does not close the list first
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(index);
                }}
                onMouseEnter={() => setActiveIndex(index)}
                className={`tap flex items-center justify-between gap-3 px-4 cursor-pointer border-s-2 ${
                  index === active
                    ? 'bg-surface-container-high dark:bg-dark-surface-container-high border-secondary dark:border-dark-secondary'
                    : 'border-transparent'
                }`}
              >
                <span lang="ar" className="font-body-arabic text-[22px] leading-snug text-on-surface dark:text-dark-on-surface truncate">
                  {item.text}
                </span>
                <span className="shrink-0 font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
                  {t(item.kind === 'chapter' ? 'suggestions.chapter' : 'suggestions.term')}
                </span>
              </li>
            ))}
          </ul>
          <p role="status" className="sr-only">
            {expanded ? t('suggestions.count', { count: suggestions.length }) : ''}
          </p>
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
