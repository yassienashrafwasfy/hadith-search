import { useLanguage } from '../i18n/useLanguage';

interface PaginationProps {
  currentPage: number;
  totalResults: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

const arabicNumber = new Intl.NumberFormat('ar-EG');

const Pagination = ({ currentPage, totalResults, pageSize, onPageChange }: PaginationProps) => {
  const { t } = useLanguage();
  const totalPages = Math.ceil(totalResults / pageSize);

  if (totalPages <= 1) return null;

  const start = (currentPage - 1) * pageSize + 1;
  const end = Math.min(currentPage * pageSize, totalResults);

  const getPages = (): (number | '...')[] => {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1);
    const pages: (number | '...')[] = [1];
    const left = Math.max(2, currentPage - 1);
    const right = Math.min(totalPages - 1, currentPage + 1);
    if (left > 2) pages.push('...');
    for (let i = left; i <= right; i++) pages.push(i);
    if (right < totalPages - 1) pages.push('...');
    pages.push(totalPages);
    return pages;
  };

  const btn =
    'tap inline-flex items-center justify-center rounded font-ui-label text-ui-label transition-colors disabled:opacity-30 disabled:cursor-not-allowed';
  const idle = 'text-on-surface-variant dark:text-dark-on-surface-variant hover:bg-surface-container dark:hover:bg-dark-surface-container';

  return (
    <nav
      aria-label={t('pagination.nav')}
      className="flex flex-col sm:flex-row items-center justify-between gap-3 py-4 border-t border-outline-variant dark:border-dark-outline-variant"
    >
      <span className="font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant tabular-nums">
        {t('pagination.showing', { start, end, total: totalResults })}
      </span>
      <div className="flex items-center gap-1 flex-wrap justify-center">
        <button
          type="button"
          onClick={() => onPageChange(currentPage - 1)}
          disabled={currentPage === 1}
          className={`${btn} ${idle}`}
          aria-label={t('pagination.prev')}
        >
          <span className="material-symbols-outlined text-[20px] rtl:-scale-x-100" aria-hidden="true">chevron_left</span>
        </button>
        {getPages().map((page, idx) =>
          page === '...' ? (
            <span key={`e-${idx}`} aria-hidden="true" className="w-8 text-center text-on-surface-variant dark:text-dark-on-surface-variant">…</span>
          ) : (
            <button
              type="button"
              key={page}
              onClick={() => onPageChange(page)}
              aria-label={t('pagination.page', { n: page })}
              aria-current={page === currentPage ? 'page' : undefined}
              className={`${btn} ${
                page === currentPage
                  ? 'bg-secondary dark:bg-dark-secondary text-on-secondary dark:text-dark-background'
                  : idle
              }`}
            >
              {arabicNumber.format(page)}
            </button>
          )
        )}
        <button
          type="button"
          onClick={() => onPageChange(currentPage + 1)}
          disabled={currentPage === totalPages}
          className={`${btn} ${idle}`}
          aria-label={t('pagination.next')}
        >
          <span className="material-symbols-outlined text-[20px] rtl:-scale-x-100" aria-hidden="true">chevron_right</span>
        </button>
      </div>
    </nav>
  );
};

export default Pagination;
