import { useLanguage } from '../i18n/useLanguage';
import FilterSelect from './FilterSelect';
import { ALGORITHM_LABELS_AR } from '../constants';
import type { ALGORITHMS } from '../types';

interface AlgorithmSelectProps {
  value: string;
  options: typeof ALGORITHMS;
  onChange: (value: string) => void;
}

const AlgorithmSelect = ({ value, options, onChange }: AlgorithmSelectProps) => {
  const { t } = useLanguage();
  return (
    <FilterSelect label={t('algorithm.label')} value={value} onChange={onChange}>
      {options.map((algo) => (
        <option key={algo.value} value={algo.value}>
          {ALGORITHM_LABELS_AR[algo.value] ?? algo.label}
        </option>
      ))}
    </FilterSelect>
  );
};

export default AlgorithmSelect;
