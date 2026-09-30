import { useLanguage } from '../i18n/useLanguage';
import type { ALGORITHMS } from '../types';

interface AlgorithmSelectProps {
  value: string;
  options: typeof ALGORITHMS;
  onChange: (value: string) => void;
}

const AlgorithmSelect = ({ value, options, onChange }: AlgorithmSelectProps) => {
  const { t } = useLanguage();
  return (
    <div className="flex flex-col gap-1">
      <label className="font-ui-label text-ui-label text-on-surface-variant dark:text-dark-on-surface-variant">
        {t('algorithm.label')}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full sm:w-auto px-4 py-2.5 bg-surface dark:bg-dark-surface border border-outline dark:border-dark-outline rounded-lg text-on-surface dark:text-dark-on-surface font-ui-label text-ui-label focus:ring-2 focus:ring-primary dark:focus:ring-dark-primary cursor-pointer"
      >
        {options.map((algo) => (
          <option key={algo.value} value={algo.value}>
            {algo.label}
          </option>
        ))}
      </select>
    </div>
  );
};

export default AlgorithmSelect;
