import { useState, useEffect } from 'react';
import { useApi } from '../api/useApi';
import { useLanguage } from '../i18n/useLanguage';
import GradeSeal from './GradeSeal';

const HOTD_ID_KEY = 'hotd_id';

const HadithOfTheDay = () => {
  const { getHadith, loading, errors } = useApi();
  const { t } = useLanguage();
  const [hadith, setHadith] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    let id = sessionStorage.getItem(HOTD_ID_KEY);
    if (!id) {
      id = String(Math.floor(Math.random() * 33738) + 1);
      sessionStorage.setItem(HOTD_ID_KEY, id);
    }
    getHadith(Number(id)).then((result) => {
      if (result) setHadith(result);
    });
  }, [getHadith]);

  if (loading.hadith) {
    return (
      <div className="w-full max-w-3xl mx-auto py-8" role="status" aria-live="polite">
        <div className="skeleton-line h-6 w-2/3 mx-auto mb-4" />
        <div className="skeleton-line h-6 w-full mb-4" />
        <div className="skeleton-line h-6 w-1/2 mx-auto" />
        <span className="sr-only">{t('loading.subtitle')}</span>
      </div>
    );
  }

  if (errors.hadith || !hadith) return null;

  const grade = (hadith.Normalized_Grade || hadith.grade) as string;
  const rawGrade = (hadith.Grade || hadith.raw_grade || grade) as string;
  const arabicText = (hadith.Arabic_Text || hadith.arabic_text || '') as string;
  const englishText = (hadith.English_Text || hadith.english_text || '') as string;
  const book = (hadith.Book || hadith.book || '') as string;
  const chapterEn = (hadith.Chapter_Title_English || hadith.chapter_title_en || '') as string;

  return (
    <section aria-labelledby="hotd-title" className="page-enter w-full max-w-3xl mx-auto folio-frame bg-surface-container-lowest dark:bg-dark-surface-container-lowest px-5 sm:px-12 py-8 sm:py-10">
      <div className="ornament mb-6" aria-hidden="true"><span className="khatam" /></div>
      <h2 id="hotd-title" className="text-center font-ui-label text-ui-label tracking-wide text-secondary dark:text-dark-secondary mb-5">
        {t('hotd.label')}
      </h2>
      <p className="arabic-text text-center text-[24px] sm:text-[32px] text-on-surface dark:text-dark-on-surface" lang="ar">
        {arabicText}
      </p>
      {englishText && (
        <p className="latin-text text-center text-[17px] sm:text-[19px] text-on-surface-variant dark:text-dark-on-surface-variant mt-5 mx-auto max-w-2xl" lang="en">
          {englishText}
        </p>
      )}
      <div className="flex items-center justify-center gap-x-4 gap-y-1 flex-wrap mt-6 font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
        {rawGrade ? <GradeSeal grade={grade} rawGrade={rawGrade} /> : null}
        <span dir="ltr" translate="no">{[book, chapterEn].filter(Boolean).join(' · ')}</span>
      </div>
    </section>
  );
};

export default HadithOfTheDay;
