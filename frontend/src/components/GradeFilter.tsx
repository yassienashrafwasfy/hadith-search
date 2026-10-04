import { useLanguage } from '../i18n/useLanguage';
import FilterSelect from './FilterSelect';

interface GradeFilterProps {
  value: string | null;
  onChange: (value: string | null) => void;
}

const grades = [
  { value: null, labelKey: 'grades.all' },
  { value: 'Sahih', labelKey: 'grades.sahih' },
  { value: 'Hasan', labelKey: 'grades.hasan' },
  { value: "Da'if (Weak)", labelKey: 'grades.daif' },
];

const GradeFilter = ({ value, onChange }: GradeFilterProps) => {
  const { t } = useLanguage();
  return (
    <FilterSelect label={t('filter.grade')} value={value ?? 'all'} onChange={(v) => onChange(v === 'all' ? null : v)}>
      {grades.map(({ value: v, labelKey }) => (
        <option key={v ?? 'all'} value={v ?? 'all'}>
          {t(labelKey)}
        </option>
      ))}
    </FilterSelect>
  );
};

export default GradeFilter;
