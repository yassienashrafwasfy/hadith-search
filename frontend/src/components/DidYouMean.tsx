import { useLanguage } from '../i18n/useLanguage';

interface DidYouMeanProps {
  suggestion: string;
  onPick: (query: string) => void;
}

// Shown under an empty result when the server offers a corrected query.
const DidYouMean = ({ suggestion, onPick }: DidYouMeanProps) => {
  const { t } = useLanguage();
  return (
    <p className="font-body-main text-body-main text-on-surface dark:text-dark-on-surface">
      {t('results.didYouMean')}{' '}
      <button
        type="button"
        onClick={() => onPick(suggestion)}
        lang="ar"
        className="font-body-arabic text-[1.2em] text-secondary dark:text-dark-secondary underline decoration-1 underline-offset-4 hover:decoration-2 cursor-pointer"
      >
        {suggestion}
      </button>
      ؟
    </p>
  );
};

export default DidYouMean;
