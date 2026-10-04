import type { SearchResult, Lang } from '../types';
import { BOOK_DISPLAY_NAMES } from '../constants';
import { useLanguage } from '../i18n/useLanguage';
import GradeSeal from './GradeSeal';

interface HadithCardProps {
  result: SearchResult;
  rank: number;
  lang: Lang;
  onClick: () => void;
  // Position inside the visible list, used only to stagger the entrance.
  index?: number;
}

const arabicNumber = new Intl.NumberFormat('ar-EG');

// A result reads like a page of a book: the rank in the margin, the grade and source above,
// the text in full below. The text is shown exactly as stored; nothing is cut or reordered.
const HadithCard = ({ result, rank, lang, onClick, index = 0 }: HadithCardProps) => {
  const { t } = useLanguage();
  const { hadith, score } = result;

  const bookEntry = BOOK_DISPLAY_NAMES[hadith.book];
  const bookName = bookEntry ? (lang === 'ar' ? bookEntry.ar : bookEntry.en) : hadith.book;
  const chapter = lang === 'ar' ? hadith.chapter_title_ar : hadith.chapter_title_en;
  const showEnglishChapter = lang === 'ar' && hadith.chapter_title_ar !== hadith.chapter_title_en;

  // A click opens the details unless the reader was selecting text to copy it.
  const handleClick = () => {
    if (window.getSelection()?.toString()) return;
    onClick();
  };

  return (
    <article
      onClick={handleClick}
      style={{ ['--i' as string]: Math.min(index, 8) }}
      className="rise folio-card group cursor-pointer grid grid-cols-[2.25rem_1fr] sm:grid-cols-[3.5rem_1fr] gap-x-3 sm:gap-x-5 bg-surface-container-lowest dark:bg-dark-surface-container-lowest border border-outline-variant dark:border-dark-outline-variant rounded px-4 sm:px-6 py-5 sm:py-6 hover:border-on-surface-variant/50 dark:hover:border-dark-outline transition-colors duration-200 has-[:focus-visible]:border-secondary dark:has-[:focus-visible]:border-dark-secondary"
    >
      <div className="flex flex-col items-center gap-1 pt-1" aria-hidden="true">
        <span className="font-body-arabic text-[28px] sm:text-[36px] leading-none text-secondary dark:text-dark-secondary">
          {arabicNumber.format(rank)}
        </span>
      </div>

      <div className="min-w-0 flex flex-col gap-3">
        <div className="flex items-center gap-x-4 gap-y-1 flex-wrap font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
          <GradeSeal grade={hadith.grade} rawGrade={hadith.raw_grade} />
          <span>{bookName}</span>
          <span dir="ltr" translate="no">{hadith.reference}</span>
        </div>

        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-h1-hadith text-[19px] sm:text-[21px] leading-snug font-normal text-secondary dark:text-dark-secondary">
              {chapter}
            </h2>
            {showEnglishChapter && (
              <p dir="ltr" className="latin-text text-[15px] text-on-surface-variant dark:text-dark-on-surface-variant mt-0.5">
                {hadith.chapter_title_en}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onClick();
            }}
            aria-haspopup="dialog"
            aria-label={`${t('results.open')}: ${hadith.reference}`}
            className="tap shrink-0 -mt-1.5 inline-flex items-center justify-center rounded text-on-surface-variant dark:text-dark-on-surface-variant group-hover:text-secondary dark:group-hover:text-dark-secondary hover:bg-surface-container dark:hover:bg-dark-surface-container transition-colors"
          >
            <span className="material-symbols-outlined text-[22px] rtl:-scale-x-100" aria-hidden="true">
              open_in_full
            </span>
          </button>
        </div>

        {lang === 'ar' ? (
          <p className="arabic-text text-[22px] sm:text-[27px] text-on-surface dark:text-dark-on-surface" lang="ar">
            {hadith.hadith_ar_text}
          </p>
        ) : (
          <p className="latin-text text-xl sm:text-2xl text-on-surface dark:text-dark-on-surface" lang="en">
            {hadith.hadith_en_text}
          </p>
        )}

        <div className="flex items-center justify-between gap-3 flex-wrap font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
          <span dir="ltr" translate="no">{hadith.in_book_reference}</span>
          <span className="tabular-nums" dir="ltr">
            {t('results.score')} {score.toFixed(4)}
          </span>
        </div>
      </div>
    </article>
  );
};

export default HadithCard;
