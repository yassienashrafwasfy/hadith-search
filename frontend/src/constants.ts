export const POPULAR_QUERIES_AR = [
  'أوقات الصلاة',
  'الصيام',
  'الصدقة',
  'الصبر',
  'المغفرة',
  'العلم',
  'الرحمة',
  'الصدق',
];

// Arabic names for the methods that have one; the other methods keep their technical names.
export const ALGORITHM_LABELS_AR: Record<string, string> = {
  'exact': 'مطابقة تامة للكلمات',
  'exact-semantic-rrf': 'مطابقة تامة + دلالي (RRF)',
};

// A grade is shown by its words, by how many of three bars are filled (strength of the
// narration) and by colour. The colour is never the only signal.
export interface GradeMeta {
  level: 0 | 1 | 2 | 3;
  text: string;
  labelKey: string;
}

export const GRADE_META: Record<string, GradeMeta> = {
  'Sahih':              { level: 3, text: 'text-grade-sahih dark:text-dark-grade-sahih', labelKey: 'grades.sahih' },
  'Hasan':              { level: 2, text: 'text-grade-hasan dark:text-dark-grade-hasan', labelKey: 'grades.hasan' },
  "Da'if (Weak)":       { level: 1, text: 'text-grade-daif dark:text-dark-grade-daif', labelKey: 'grades.daif' },
  'Maudu (Fabricated)': { level: 0, text: 'text-grade-fabricated dark:text-dark-grade-fabricated', labelKey: 'grades.fabricated' },
  'Unknown':            { level: 0, text: 'text-grade-unknown dark:text-dark-grade-unknown', labelKey: 'grades.unknown' },
};

export const BOOK_DISPLAY_NAMES: Record<string, { en: string; ar: string }> = {
  'Sahih al-Bukhari': { en: 'Sahih al-Bukhari', ar: 'صحيح البخاري' },
  'Sahih Muslim':     { en: 'Sahih Muslim',     ar: 'صحيح مسلم' },
  "Jami` at-Tirmidhi": { en: "Jami' at-Tirmidhi", ar: 'جامع الترمذي' },
  'Sunan Abi Dawud':  { en: 'Sunan Abi Dawud',  ar: 'سنن أبي داود' },
  'Sunan Ibn Majah':   { en: 'Sunan Ibn Majah',   ar: 'سنن ابن ماجه' },
  "Sunan an-Nasa'i":  { en: "Sunan an-Nasa'i",  ar: 'سنن النسائي' },
};
