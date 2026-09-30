import { useEffect, useState } from 'react';
import { getSearchMethods } from './services';
import { ALGORITHMS } from '../types';

const PREFERRED = 'bm25-prf';

// The algorithms the server offers, in the order the UI lists them. Until the answer
// arrives (or if the request fails) the list is empty, so nothing unsupported is shown.
export const useSearchMethods = () => {
  const [available, setAvailable] = useState<string[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getSearchMethods(controller.signal)
      .then(setAvailable)
      .catch(() => {
        if (!controller.signal.aborted) setAvailable([]);
      });
    return () => controller.abort();
  }, []);

  const options = ALGORITHMS.filter((algo) => available?.includes(algo.value));
  const fallback = options.find((algo) => algo.value === PREFERRED) ?? options[0];
  return { options, fallback: fallback?.value, ready: available !== null };
};
