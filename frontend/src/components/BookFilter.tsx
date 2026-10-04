import { useLanguage } from '../i18n/useLanguage';
import FilterSelect from './FilterSelect';

interface BookFilterProps {
  value: string | null;
  onChange: (value: string | null) => void;
}

const books = [
  { value: null, labelKey: 'books.all' },
  { value: 'Sahih al-Bukhari', labelKey: 'books.sahih-al-bukhari' },
  { value: 'Sahih Muslim', labelKey: 'books.sahih-muslim' },
  { value: 'Jami` at-Tirmidhi', labelKey: 'books.jami-al-tirmidhi' },
  { value: 'Sunan Abi Dawud', labelKey: 'books.sunan-abu-dawud' },
  { value: 'Sunan Ibn Majah', labelKey: 'books.sunan-ibn-majah' },
  { value: "Sunan an-Nasa'i", labelKey: 'books.sunan-al-nasai' },
];

const BookFilter = ({ value, onChange }: BookFilterProps) => {
  const { t } = useLanguage();
  return (
    <FilterSelect label={t('filter.book')} value={value ?? 'all'} onChange={(v) => onChange(v === 'all' ? null : v)}>
      {books.map(({ value: v, labelKey }) => (
        <option key={v ?? 'all'} value={v ?? 'all'}>
          {t(labelKey)}
        </option>
      ))}
    </FilterSelect>
  );
};

export default BookFilter;
