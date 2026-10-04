import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { API_BASE_URL } from '../api/config';
import { useAuth } from '../api/AuthContext';
import { errorKey, ensureOk } from '../api/errors';
import { useLanguage } from '../i18n/useLanguage';
import LoadingSpinner from '../components/LoadingSpinner';

interface Hadith {
  hadith_id: number;
  arabic_hadith: string;
  english_hadith: string;
  book: string;
  normalized_grade: string;
  reference: string;
  in_book_reference: string;
}

interface AnnotationState {
  query_id: string;
  query: string;
  current_index: number;
  total: number;
  pooled_hadiths: Hadith[];
  labels: Record<string, number>;
}

const GRADE_COLORS: Record<number, string> = {
  0: 'text-red-700 dark:text-red-300',
  1: 'text-amber-800 dark:text-amber-300',
  2: 'text-green-800 dark:text-green-300',
};

const DevAnnotationSessionPage = () => {
  const { t } = useLanguage();
  const { queryId } = useParams<{ queryId: string }>();
  const navigate = useNavigate();
  const { token, loading: authLoading, authFetch } = useAuth();
  const [state, setState] = useState<AnnotationState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flashGrade, setFlashGrade] = useState<number | null>(null);
  const lastSavedRef = useRef<number>(0);
  const savingRef = useRef(false);

  useEffect(() => {
    if (authLoading) return;
    if (!token) {
      navigate('/dev/annotation/signin');
      return;
    }

    let mounted = true;
    const fetchData = async () => {
      if (!queryId) return;
      setLoading(true);
      setError(null);
      try {
        const response = await authFetch(`${API_BASE_URL}/api/v1/assignments/${queryId}`);
        if (response.status === 401) {
          navigate('/dev/annotation/signin');
          return;
        }
        ensureOk(response);
        const data = await response.json();
        if (mounted) setState(data);
      } catch (err) {
        if (mounted) setError(errorKey(err));
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchData();
    return () => { mounted = false };
  }, [token, authLoading, navigate, authFetch, queryId]);

  const saveLabel = async (index: number, label: number) => {
    if (!queryId || !state) return;
    const hadith = state.pooled_hadiths[index];
    if (!hadith) return;
    if (savingRef.current) return;

    setFlashGrade(label);
    setSaving(true);
    savingRef.current = true;
    try {
      const base = `${API_BASE_URL}/api/v1/assignments/${queryId}`;
      const json = { 'Content-Type': 'application/json' };
      const saved = await authFetch(`${base}/labels/${hadith.hadith_id}`, {
        method: 'PUT',
        headers: json,
        body: JSON.stringify({ label })
      });
      ensureOk(saved);
      // Saving a label no longer moves the cursor; do that explicitly.
      await authFetch(`${base}/progress`, {
        method: 'PUT',
        headers: json,
        body: JSON.stringify({ index: Math.min(index + 1, state.total - 1) })
      });
      lastSavedRef.current = Date.now();

      setFlashGrade(null);
      setState(prev => prev ? {
        ...prev,
        labels: { ...prev.labels, [String(hadith.hadith_id)]: label },
        current_index: index + 1
      } : null);
      setSaving(false);
      savingRef.current = false;
    } catch (err) {
      console.error('Failed to save label:', err);
      setFlashGrade(null);
      setSaving(false);
      savingRef.current = false;
    }
  };

  const navigateTo = async (index: number) => {
    if (!queryId || !state) return;
    if (index < 0 || index >= state.total) return;

    try {
      await authFetch(`${API_BASE_URL}/api/v1/assignments/${queryId}/progress`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index })
      });
      setState(prev => prev ? { ...prev, current_index: index } : null);
    } catch (err) {
      console.error('Failed to navigate:', err);
    }
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!state) return;
      if (savingRef.current) return;

      if (e.key === 'ArrowRight') {
        navigateTo(state.current_index + 1);
      } else if (e.key === 'ArrowLeft') {
        navigateTo(state.current_index - 1);
      } else if (e.key === '0' || e.key === '1' || e.key === '2') {
        saveLabel(state.current_index, parseInt(e.key));
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [state]);

  if (authLoading || loading) return <LoadingSpinner />;

  if (error) {
    return (
      <main className="flex-grow w-full max-w-[90rem] mx-auto px-4 sm:px-6 lg:px-8 py-10 flex flex-col gap-4 page-enter">
        <div className="bg-error-container text-on-error-container p-4 rounded-lg">
          {t(error)}
        </div>
        <button
          onClick={() => navigate('/dev/annotation')}
          className="text-primary dark:text-dark-primary hover:underline"
        >
          {t('annotation.backToList')}
        </button>
      </main>
    );
  }

  if (!state) return <LoadingSpinner />;

  const currentHadith = state.pooled_hadiths[state.current_index];
  const currentLabel = currentHadith ? state.labels[String(currentHadith.hadith_id)] : undefined;

  const gradeCounts = [0, 1, 2].map(g => ({
    grade: g,
    count: Object.values(state.labels).filter(l => l === g).length,
  }));

  const arNum = new Intl.NumberFormat('ar-EG');
  const SCORES = [
    { n: 0, key: 'annotation.score0', on: 'border-red-700 bg-red-700 text-white', cur: 'border-red-700 bg-red-50 dark:bg-red-900/20' },
    { n: 1, key: 'annotation.score1', on: 'border-amber-700 bg-amber-700 text-white', cur: 'border-amber-700 bg-amber-50 dark:bg-amber-900/20' },
    { n: 2, key: 'annotation.score2', on: 'border-green-700 bg-green-700 text-white', cur: 'border-green-700 bg-green-50 dark:bg-green-900/20' },
  ];
  const navBtn =
    'tap inline-flex items-center gap-2 px-4 rounded border border-outline dark:border-dark-outline text-on-surface dark:text-dark-on-surface font-ui-label text-ui-label hover:bg-surface-container dark:hover:bg-dark-surface-container disabled:opacity-40 disabled:cursor-not-allowed transition-colors';
  const progress = Math.min(100, ((state.current_index + 1) / state.total) * 100);

  return (
    <main className="flex-grow w-full max-w-[64rem] mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-10 flex flex-col gap-6 page-enter">
      <div className="flex items-center justify-between gap-3">
        <button type="button" onClick={() => navigate('/dev/annotation')} className={navBtn}>
          <span className="material-symbols-outlined text-[20px] rtl:-scale-x-100" aria-hidden="true">arrow_back</span>
          {t('annotation.backToList')}
        </button>
        <div className="flex items-center gap-3 font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant tabular-nums">
          <span role="status">{saving ? t('annotation.saving') : ''}</span>
          <span>{t('annotation.position', { current: arNum.format(state.current_index + 1), total: arNum.format(state.total) })}</span>
        </div>
      </div>

      <div
        role="progressbar"
        aria-label={t('annotation.progress')}
        aria-valuemin={1}
        aria-valuemax={state.total}
        aria-valuenow={state.current_index + 1}
        className="w-full h-1.5 bg-surface-variant dark:bg-dark-surface-variant rounded-full overflow-hidden"
      >
        <div className="bg-secondary dark:bg-dark-secondary h-full transition-[width] duration-300" style={{ width: `${progress}%` }} />
      </div>

      <header>
        <p className="font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">{t('annotation.query')}</p>
        <h1 className="font-display-lg text-[28px] sm:text-[36px] leading-snug text-on-surface dark:text-dark-on-surface">{state.query}</h1>
      </header>

      {Object.keys(state.labels).length > 0 && (
        <ul aria-label={t('annotation.counts')} className="flex items-center gap-6 flex-wrap font-ui-caption text-ui-caption">
          {gradeCounts.map(({ grade, count }) => (
            <li key={grade} className={GRADE_COLORS[grade]}>
              {t(`annotation.score${grade}`)}: {arNum.format(count)}
            </li>
          ))}
        </ul>
      )}

      <section className="folio-card bg-surface-container-lowest dark:bg-dark-surface-container-lowest border border-outline-variant dark:border-dark-outline-variant rounded px-4 sm:px-8 py-6">
        {currentHadith ? (
          <div className="flex flex-col gap-5">
            {currentHadith.book && (
              <p className="flex flex-wrap items-center gap-x-4 gap-y-1 font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
                <span dir="ltr" translate="no">
                  {[currentHadith.book, currentHadith.normalized_grade, currentHadith.reference].filter(Boolean).join(' · ')}
                </span>
                <span className="tabular-nums" dir="ltr">#{currentHadith.hadith_id}</span>
              </p>
            )}
            <div>
              <h2 className="font-ui-label text-ui-label text-secondary dark:text-dark-secondary mb-2">{t('annotation.arabic')}</h2>
              <p className="arabic-text text-[24px] sm:text-[28px] text-on-surface dark:text-dark-on-surface whitespace-pre-wrap" lang="ar">
                {currentHadith.arabic_hadith}
              </p>
            </div>
            <div className="ornament" aria-hidden="true"><span className="khatam" /></div>
            <div>
              <h2 className="font-ui-label text-ui-label text-secondary dark:text-dark-secondary mb-2">{t('annotation.english')}</h2>
              <p className="latin-text text-[18px] sm:text-[20px] text-on-surface dark:text-dark-on-surface whitespace-pre-wrap" lang="en">
                {currentHadith.english_hadith}
              </p>
            </div>
            {currentLabel !== undefined && (
              <p className="font-ui-label text-ui-label text-on-surface-variant dark:text-dark-on-surface-variant">
                {t('annotation.current')}:{' '}
                <span className={`font-semibold ${GRADE_COLORS[currentLabel]}`}>{t(`annotation.score${currentLabel}`)}</span>
              </p>
            )}
          </div>
        ) : (
          <p className="text-center py-12 text-on-surface-variant dark:text-dark-on-surface-variant">{t('annotation.done')}</p>
        )}
      </section>

      <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4">
        <button
          type="button"
          onClick={() => navigateTo(state.current_index - 1)}
          disabled={state.current_index === 0}
          className={navBtn}
        >
          <span className="material-symbols-outlined text-[20px] rtl:-scale-x-100" aria-hidden="true">chevron_right</span>
          {t('annotation.previous')}
        </button>

        <div role="group" aria-label={t('annotation.current')} className="grid grid-cols-3 gap-2 sm:gap-3">
          {SCORES.map(({ n, key, on, cur }) => (
            <button
              type="button"
              key={n}
              onClick={() => saveLabel(state.current_index, n)}
              disabled={!currentHadith || saving}
              aria-pressed={currentLabel === n}
              className={`flex flex-col items-center gap-1 px-3 sm:px-6 py-3 rounded border-2 transition-colors disabled:opacity-50 ${
                flashGrade === n
                  ? on
                  : currentLabel === n
                  ? cur
                  : 'border-outline dark:border-dark-outline hover:bg-surface-container dark:hover:bg-dark-surface-container'
              }`}
            >
              <span className={`text-lg font-bold ${flashGrade === n ? 'text-white' : 'text-on-surface dark:text-dark-on-surface'}`}>{arNum.format(n)}</span>
              <span className={`text-xs ${flashGrade === n ? 'text-white' : 'text-on-surface-variant dark:text-dark-on-surface-variant'}`}>{t(key)}</span>
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={() => navigateTo(state.current_index + 1)}
          disabled={state.current_index >= state.total - 1}
          className={`${navBtn} justify-end`}
        >
          {t('annotation.next')}
          <span className="material-symbols-outlined text-[20px] rtl:-scale-x-100" aria-hidden="true">chevron_left</span>
        </button>
      </div>

      <p className="text-center font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant">
        <span className="font-semibold">{t('annotation.shortcuts')}:</span> {t('annotation.shortcutsHelp')}
      </p>
    </main>
  );
};

export default DevAnnotationSessionPage;
