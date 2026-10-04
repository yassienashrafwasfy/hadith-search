// Turns any failure into a translation key for a friendly Arabic message.
// The numeric status stays on ApiError for code logic (e.g. 401 handling) but is never rendered,
// and neither is the server's problem+json title/detail or any exception text.

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export type ErrorKey =
  | 'error.network'
  | 'error.invalid'
  | 'error.unauthorized'
  | 'error.forbidden'
  | 'error.notFound'
  | 'error.conflict'
  | 'error.tooMany'
  | 'error.generic';

export const statusToErrorKey = (status: number): ErrorKey => {
  if (status === 400 || status === 422) return 'error.invalid';
  if (status === 401) return 'error.unauthorized';
  if (status === 403) return 'error.forbidden';
  if (status === 404) return 'error.notFound';
  if (status === 409) return 'error.conflict';
  if (status === 429) return 'error.tooMany';
  return 'error.generic';
};

export const errorKey = (err: unknown): ErrorKey => {
  if (err instanceof ApiError) return statusToErrorKey(err.status);
  // fetch() rejects with a TypeError when the network is down or the server is unreachable.
  if (err instanceof TypeError || (typeof navigator !== 'undefined' && navigator.onLine === false)) {
    return 'error.network';
  }
  return 'error.generic';
};

// Throws an ApiError carrying only the status when the response is not ok.
export const ensureOk = (response: Response): void => {
  if (!response.ok) throw new ApiError(`request failed (${response.status})`, response.status);
};
