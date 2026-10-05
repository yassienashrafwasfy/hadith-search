import { useEffect, useState } from 'react';
import { getSuggestions } from '../api/services';
import type { Suggestion } from '../types';

export const SUGGESTION_DEBOUNCE_MS = 200;
const MIN_LENGTH = 2;
const MAX_LENGTH = 100; // the server refuses a longer q

// Autocomplete entries for what the user has typed so far. The request waits until typing
// pauses and the previous one is cancelled, so only the latest text is answered. The site is
// Arabic only, so English entries are left out. Suggestions are a convenience: a failed request
// simply shows none.
export const useSuggestions = (query: string, enabled: boolean): Suggestion[] => {
  const [settled, setSettled] = useState<{ q: string; items: Suggestion[] }>({ q: '', items: [] });
  const q = query.trim();
  const wanted = enabled && q.length >= MIN_LENGTH && q.length <= MAX_LENGTH;

  useEffect(() => {
    if (!wanted) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      getSuggestions(q, controller.signal)
        .then((items) => setSettled({ q, items: items.filter((item) => item.lang === 'ar') }))
        .catch(() => undefined);
    }, SUGGESTION_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [q, wanted]);

  // An answer for older text is never shown for the current text.
  return wanted && settled.q === q ? settled.items : [];
};
