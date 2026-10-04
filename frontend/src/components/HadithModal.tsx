import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { SearchResult, Lang } from '../types';
import { BOOK_DISPLAY_NAMES } from '../constants';
import { useLanguage } from '../i18n/useLanguage';
import GradeSeal from './GradeSeal';

interface HadithModalProps {
  result: SearchResult | null;
  rank?: number;
  lang: Lang;
  onClose: () => void;
}

const arabicNumber = new Intl.NumberFormat('ar-EG');
const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

const HadithModal = ({ result, rank, lang, onClose }: HadithModalProps) => {
  const { t } = useLanguage();
  const dialogRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!result) return;
    const opener = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    dialog?.querySelector<HTMLElement>('button')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      // Keep Tab inside the dialog.
      if (e.key !== 'Tab' || !dialog) return;
      const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
      opener?.focus?.();
    };
  }, [result, onClose]);

  if (!result) return null;

  const { hadith, score } = result;
  const bookEntry = BOOK_DISPLAY_NAMES[hadith.book];
  const bookName = bookEntry ? (lang === 'ar' ? bookEntry.ar : bookEntry.en) : hadith.book;
  const chapter = lang === 'ar' ? hadith.chapter_title_ar : hadith.chapter_title_en;
  const otherChapter = lang === 'ar' ? hadith.chapter_title_en : hadith.chapter_title_ar;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${hadith.hadith_ar_text}\n\n${hadith.hadith_en_text}\n\n${hadith.reference}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard may be blocked; nothing to do */
    }
  };

  // Rendered on <body>: an animated page wrapper would otherwise become the containing block of `fixed`.
  return createPortal(
    <div
      role="presentation"
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-6 bg-black/60 dark:bg-black/70 animate-fade-in"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('hadith.dialog')}
        className="animate-scale-in contain-scroll bg-surface dark:bg-dark-surface w-full sm:max-w-3xl max-h-[92dvh] overflow-y-auto rounded-t-lg sm:rounded border border-outline-variant dark:border-dark-outline-variant border-t-[3px] border-t-secondary dark:border-t-dark-secondary"
      >
        <div className="sticky top-0 z-10 bg-surface dark:bg-dark-surface border-b border-outline-variant dark:border-dark-outline-variant px-4 sm:px-6 py-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-x-4 gap-y-1 flex-wrap font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
            {rank !== undefined && (
              <span className="font-body-arabic text-[26px] leading-none text-secondary dark:text-dark-secondary" aria-label={`${t('results.rank')} ${rank}`}>
                {arabicNumber.format(rank)}
              </span>
            )}
            <GradeSeal grade={hadith.grade} rawGrade={hadith.raw_grade} />
            <span>{bookName}</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('hadith.close')}
            className="tap shrink-0 inline-flex items-center justify-center rounded text-on-surface-variant dark:text-dark-on-surface-variant hover:bg-surface-container dark:hover:bg-dark-surface-container transition-colors"
          >
            <span className="material-symbols-outlined text-[24px]" aria-hidden="true">close</span>
          </button>
        </div>

        <div className="px-4 sm:px-8 py-6 flex flex-col gap-6">
          <div>
            <h2 className="font-h1-hadith text-[21px] sm:text-[24px] leading-snug font-normal text-secondary dark:text-dark-secondary">
              {chapter}
            </h2>
            {hadith.chapter_title_ar !== hadith.chapter_title_en && (
              <p
                className={`${lang === 'ar' ? 'latin-text' : 'font-h1-hadith'} text-[15px] text-on-surface-variant dark:text-dark-on-surface-variant mt-1`}
                dir={lang === 'ar' ? 'ltr' : 'rtl'}
              >
                {otherChapter}
              </p>
            )}
          </div>

          <p className="arabic-text text-[24px] sm:text-[30px] text-on-surface dark:text-dark-on-surface" lang="ar">
            {hadith.hadith_ar_text}
          </p>
          <div className="ornament" aria-hidden="true"><span className="khatam" /></div>
          <p className="latin-text text-[19px] sm:text-[21px] text-on-surface dark:text-dark-on-surface" lang="en">
            {hadith.hadith_en_text}
          </p>

          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 border-t border-outline-variant dark:border-dark-outline-variant pt-4 font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
            <dt>{t('results.reference')}</dt>
            <dd dir="ltr" translate="no" className="text-on-surface dark:text-dark-on-surface text-start">{hadith.reference}</dd>
            <dt>{t('results.inBookRef')}</dt>
            <dd dir="ltr" translate="no" className="text-on-surface dark:text-dark-on-surface text-start">{hadith.in_book_reference}</dd>
            <dt>{t('results.score')}</dt>
            <dd dir="ltr" className="tabular-nums text-on-surface dark:text-dark-on-surface text-start">{score.toFixed(4)}</dd>
          </dl>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={copy}
              className="tap inline-flex items-center gap-2 px-4 rounded border border-outline dark:border-dark-outline text-on-surface dark:text-dark-on-surface font-ui-label text-ui-label hover:bg-surface-container dark:hover:bg-dark-surface-container transition-colors"
            >
              <span className="material-symbols-outlined text-[20px]" aria-hidden="true">{copied ? 'check' : 'content_copy'}</span>
              {copied ? t('hadith.copied') : t('hadith.copy')}
            </button>
            <span role="status" className="sr-only">{copied ? t('hadith.copied') : ''}</span>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default HadithModal;
