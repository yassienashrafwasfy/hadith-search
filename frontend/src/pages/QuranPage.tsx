import { useRef, useState } from 'react';
import { askQuran, type QuranAnswer, type QuranCitation } from '../api/services';
import { errorKey } from '../api/errors';
import { useLanguage } from '../i18n/useLanguage';
import ErrorBanner from '../components/ErrorBanner';
import LoadingSpinner from '../components/LoadingSpinner';

const EXAMPLES = ['ما هي آية الكرسي؟', 'ما فضل الصبر؟', 'ماذا يقول القرآن عن الصيام؟'];

const CitationCard = ({ citation }: { citation: QuranCitation }) => {
  const { t } = useLanguage();
  const label = citation.web ? t('quran.web') : t(`quran.source.${citation.source}`);
  return (
    <li className="border border-outline-variant dark:border-dark-outline-variant rounded p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3 font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
        <span>{label}</span>
        <span dir="ltr">{citation.web ? citation.attribution : citation.ref}</span>
      </div>
      <p lang="ar" dir="rtl" className="font-body-main text-body-main text-on-surface dark:text-dark-on-surface whitespace-pre-line max-h-64 overflow-y-auto">
        {citation.text}
      </p>
    </li>
  );
};

const QuranPage = () => {
  const { t } = useLanguage();
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<QuranAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [searchingWeb, setSearchingWeb] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const ask = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setLoading(true);
    setError(null);
    setResult(null);
    setSearchingWeb(false);
    try {
      setResult(
        await askQuran(trimmed, controller.signal, (step) => {
          if (step === 'web_search') setSearchingWeb(true);
        }),
      );
    } catch (err) {
      if (!controller.signal.aborted) setError(errorKey(err));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };

  return (
    <main className="flex-grow flex flex-col items-center px-4 sm:px-6 md:px-10 pt-12 pb-16 w-full max-w-container-max-width mx-auto">
      <div className="text-center max-w-3xl flex flex-col items-center gap-4 mb-8 page-enter">
        <span className="khatam" aria-hidden="true" />
        <h1 className="font-display-lg text-display-lg text-on-surface dark:text-dark-on-surface">{t('quran.title')}</h1>
        <p className="font-body-main text-body-main text-on-surface-variant dark:text-dark-on-surface-variant">{t('quran.subtitle')}</p>
      </div>

      <form
        className="w-full max-w-3xl flex gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(question);
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={t('quran.placeholder')}
          maxLength={500}
          lang="ar"
          dir="rtl"
          className="flex-grow tap px-4 border border-outline-variant dark:border-dark-outline-variant rounded bg-transparent text-on-surface dark:text-dark-on-surface font-body-main text-body-main"
        />
        <button
          type="submit"
          disabled={loading || !question.trim()}
          className="tap px-6 rounded bg-primary text-on-primary dark:bg-dark-primary dark:text-dark-on-primary font-ui-label text-ui-label disabled:opacity-50"
        >
          {t('quran.ask')}
        </button>
      </form>

      <div className="w-full max-w-3xl mt-3 flex flex-wrap items-center gap-2 font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
        <span>{t('quran.examples')}</span>
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => {
              setQuestion(example);
              void ask(example);
            }}
            className="px-3 py-1 border border-outline-variant dark:border-dark-outline-variant rounded hover:border-on-surface dark:hover:border-dark-on-surface"
          >
            {example}
          </button>
        ))}
      </div>

      <div className="w-full max-w-3xl mt-8 flex flex-col gap-6" aria-live="polite">
        {loading && (
          <div className="flex flex-col items-center gap-3 text-on-surface-variant dark:text-dark-on-surface-variant">
            <LoadingSpinner />
            <p>{t('quran.thinking')}</p>
            {searchingWeb && (
              <p className="motion-safe:animate-ink-pulse" role="status">
                {t('quran.searchingWeb')}
                <span aria-hidden="true">…</span>
              </p>
            )}
          </div>
        )}
        {error && <ErrorBanner message={error} />}
        {result && (
          <>
            <p lang="ar" dir="rtl" className="font-body-main text-body-main text-on-surface dark:text-dark-on-surface whitespace-pre-line">
              {result.fallback ? t('quran.noInfo') : result.answer}
            </p>
            {result.fallback && (
              <p lang="ar" dir="rtl" className="text-on-surface-variant dark:text-dark-on-surface-variant">{result.answer}</p>
            )}
            {result.citations.length > 0 && (
              <section>
                <h2 className="font-ui-label text-ui-label text-on-surface dark:text-dark-on-surface mb-3">{t('quran.sources')}</h2>
                <ul className="flex flex-col gap-3">
                  {result.citations.map((citation) => (
                    <CitationCard key={`${citation.source}-${citation.ref}`} citation={citation} />
                  ))}
                </ul>
              </section>
            )}
            {result.trace.length > 0 && (
              <details className="font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
                <summary>{t('quran.trace')}</summary>
                <ol dir="ltr" className="list-decimal ps-6 mt-2">
                  {result.trace.map((step, index) => (
                    <li key={index}>{step}</li>
                  ))}
                </ol>
              </details>
            )}
          </>
        )}
      </div>
    </main>
  );
};

export default QuranPage;
