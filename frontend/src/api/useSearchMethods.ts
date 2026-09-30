import { useEffect, useState } from 'react';
import { getSearchMethods, type SearchMethodInfo } from './services';
import { ALGORITHMS, type Lang } from '../types';

const PREFERRED = 'bm25-prf';

// The algorithms the server offers for a language, in the order the UI lists them. Until the
// answer arrives (or if the request fails) the list is empty, so nothing unsupported is shown.
export const useSearchMethods = (lang: Lang) => {
  const [methods, setMethods] = useState<SearchMethodInfo[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getSearchMethods(controller.signal)
      .then(setMethods)
      .catch(() => {
        if (!controller.signal.aborted) setMethods([]);
      });
    return () => controller.abort();
  }, []);

  const optionsFor = (forLang: Lang) =>
    ALGORITHMS.filter((algo) =>
      methods?.some((m) => m.slug === algo.value && m.languages.includes(forLang)),
    );
  const options = optionsFor(lang);
  // The wanted method if it works for the language, otherwise one that does.
  const resolve = (wanted: string, forLang: Lang = lang) => {
    const offered = optionsFor(forLang);
    if (offered.some((o) => o.value === wanted)) return wanted;
    return (offered.find((o) => o.value === PREFERRED) ?? offered[0])?.value ?? wanted;
  };
  return { options, resolve, ready: methods !== null };
};
