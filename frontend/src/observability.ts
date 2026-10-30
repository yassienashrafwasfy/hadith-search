import type { ExceptionEvent, TransportItem } from '@grafana/faro-web-sdk';

// nginx forwards this same-origin path to Alloy's Faro receiver (docs/HANDOFF.md item 47), so the
// Content-Security-Policy (connect-src 'self') needs no new origin.
const COLLECT_URL = '/faro/collect';
const SEARCH_TEXT = /([?&])q=[^&#\s"']*/g;

/** Replace the search text (?q=...) in a URL or message, so it never leaves the browser. */
export function withoutSearchText(text: string): string {
  return text.replace(SEARCH_TEXT, '$1q=-');
}

function scrub(item: TransportItem): TransportItem {
  const page = item.meta.page;
  if (page?.url) page.url = withoutSearchText(page.url);
  const exception = item.payload as Partial<ExceptionEvent>;
  if (typeof exception.value === 'string') exception.value = withoutSearchText(exception.value);
  return item;
}

/**
 * Report JavaScript errors and Core Web Vitals (LCP, INP, CLS) to Grafana through Faro.
 * Production builds only; no console capture, no session tracking, no tracing. The SDK is a separate
 * chunk loaded after the page starts, so it does not slow the first render.
 */
export async function startObservability(): Promise<void> {
  if (!import.meta.env.PROD) return;
  const { ErrorsInstrumentation, initializeFaro, WebVitalsInstrumentation } = await import(
    '@grafana/faro-web-sdk'
  );
  initializeFaro({
    url: COLLECT_URL,
    app: {
      name: 'hadith-search',
      version: import.meta.env.VITE_RELEASE || 'unknown',
      environment: 'production',
    },
    instrumentations: [new ErrorsInstrumentation(), new WebVitalsInstrumentation()],
    sessionTracking: { enabled: false },
    pageTracking: { generatePageId: (location) => location.pathname },
    beforeSend: scrub,
  });
}
