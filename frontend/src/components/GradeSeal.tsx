import { GRADE_META } from '../constants';
import { useLanguage } from '../i18n/useLanguage';

interface GradeSealProps {
  grade: string;
  // The grade text as the source gives it. It is shown as is, never rewritten.
  rawGrade?: string;
  className?: string;
}

// Three small bars: filled count = strength of the narration. Fabricated and unknown show none.
export const GradeBars = ({ level, className = '' }: { level: number; className?: string }) => (
  <span aria-hidden="true" className={`inline-flex items-end gap-[2px] ${className}`}>
    {[1, 2, 3].map((n) => (
      <span
        key={n}
        className={`block w-[5px] h-[14px] border border-current ${n <= level ? 'bg-current' : ''}`}
      />
    ))}
  </span>
);

const GradeSeal = ({ grade, rawGrade, className = '' }: GradeSealProps) => {
  const { t } = useLanguage();
  const meta = GRADE_META[grade] ?? GRADE_META['Unknown'];
  const label = rawGrade || grade;
  return (
    <span
      className={`inline-flex items-center gap-2 font-ui-label text-ui-label font-semibold ${meta.text} ${className}`}
      title={t(meta.labelKey)}
    >
      <GradeBars level={meta.level} />
      <span dir="ltr" translate="no">{label}</span>
      <span className="sr-only">{`(${t(meta.labelKey)})`}</span>
    </span>
  );
};

export default GradeSeal;
