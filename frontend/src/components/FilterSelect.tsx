import { useId, type ReactNode } from 'react';

interface FilterSelectProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  className?: string;
}

// A labelled native select. Native controls keep keyboard and screen reader behaviour for free.
const FilterSelect = ({ label, value, onChange, children, className = '' }: FilterSelectProps) => {
  const id = useId();
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full min-h-[44px] ps-3 pe-2 bg-surface-container-lowest dark:bg-dark-surface-container-lowest border border-on-surface-variant/60 dark:border-dark-outline rounded text-on-surface dark:text-dark-on-surface font-ui-label text-ui-label cursor-pointer hover:border-on-surface dark:hover:border-dark-on-surface transition-colors"
      >
        {children}
      </select>
    </div>
  );
};

export default FilterSelect;
