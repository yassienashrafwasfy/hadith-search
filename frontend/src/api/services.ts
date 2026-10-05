import { API_BASE_URL } from './config';
import { ApiError } from './errors';
import { validateSearchResponse, validateBenchmarkResults } from './validators';
import type { SearchResponse, BenchmarkResults, SearchRequest, Lang, Suggestion } from '../types';

export { ApiError };

const API_V1 = `${API_BASE_URL}/api/v1`;

// The server reports timing in the Server-Timing header ("search;dur=12.3"), which keeps
// the body identical between calls so it can be cached and revalidated with an ETag.
const readServerTiming = (response: Response): number | undefined => {
  const match = /dur=([\d.]+)/.exec(response.headers.get('Server-Timing') ?? '');
  return match ? Number(match[1]) : undefined;
};

export const searchHadiths = async (
  algorithm: string,
  request: SearchRequest,
  signal?: AbortSignal
): Promise<SearchResponse> => {
  const params = new URLSearchParams({ q: request.query, method: algorithm, lang: request.lang });
  if (request.grade_filter) params.set('grade_filter', request.grade_filter);
  if (request.book_filter) params.set('book_filter', request.book_filter);
  const response = await fetch(`${API_V1}/searches?${params}`, { signal });

  if (!response.ok) {
    throw new ApiError(`Search failed: ${response.status}`, response.status);
  }

  const data = validateSearchResponse(await response.json());
  return { ...data, response_time_ms: readServerTiming(response) };
};

export const getBenchmarkResults = async (signal?: AbortSignal): Promise<BenchmarkResults> => {
  const response = await fetch(`${API_V1}/benchmark/results`, { signal });

  if (!response.ok) {
    throw new ApiError(`Benchmark fetch failed: ${response.status}`, response.status);
  }

  const data = await response.json();
  return validateBenchmarkResults(data);
};

export const getBenchmarkQrels = async (signal?: AbortSignal) => {
  const response = await fetch(`${API_V1}/benchmark/qrels`, { signal });

  if (!response.ok) {
    throw new ApiError(`Qrels fetch failed: ${response.status}`, response.status);
  }

  return response.json();
};

export const getHadithById = async (id: number, signal?: AbortSignal) => {
  const response = await fetch(`${API_V1}/hadiths/${id}`, { signal });

  if (!response.ok) {
    throw new ApiError(`Hadith fetch failed: ${response.status}`, response.status);
  }

  return response.json();
};

export const getAnnotationQueries = async (headers?: HeadersInit, signal?: AbortSignal) => {
  const response = await fetch(`${API_V1}/assignments`, { headers, signal });

  if (!response.ok) {
    throw new ApiError(`Assignments fetch failed: ${response.status}`, response.status);
  }

  return response.json();
};

export const getAnnotationCurrent = async (queryId: string, headers?: HeadersInit, signal?: AbortSignal) => {
  const response = await fetch(`${API_V1}/assignments/${queryId}`, { headers, signal });

  if (!response.ok) {
    throw new ApiError(`Assignment fetch failed: ${response.status}`, response.status);
  }

  return response.json();
};

export const saveAnnotationLabel = async (queryId: string, hadithId: number, label: number, headers?: HeadersInit, signal?: AbortSignal) => {
  const response = await fetch(`${API_V1}/assignments/${queryId}/labels/${hadithId}`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ label }),
    signal
  });

  if (!response.ok) {
    throw new ApiError(`Label save failed: ${response.status}`, response.status);
  }

  return response.json();
};

export const navigateAnnotation = async (queryId: string, index: number, headers?: HeadersInit, signal?: AbortSignal) => {
  const response = await fetch(`${API_V1}/assignments/${queryId}/progress`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ index }),
    signal
  });

  if (!response.ok) {
    throw new ApiError(`Progress save failed: ${response.status}`, response.status);
  }

  return response.json();
};

export interface SearchMethodInfo {
  slug: string;
  languages: Lang[];
}

// The search methods this server has switched on, with the languages each supports
// (GET /api/v1/search-methods).
export const getSearchMethods = async (signal?: AbortSignal): Promise<SearchMethodInfo[]> => {
  const response = await fetch(`${API_V1}/search-methods`, { signal });

  if (!response.ok) {
    throw new ApiError(`Search methods fetch failed: ${response.status}`, response.status);
  }

  const data = await response.json();
  return data.methods as SearchMethodInfo[];
};

// Autocomplete (GET /api/v1/suggestions): vocabulary words and chapter titles for a partial query.
export const getSuggestions = async (query: string, signal?: AbortSignal): Promise<Suggestion[]> => {
  const response = await fetch(`${API_V1}/suggestions?${new URLSearchParams({ q: query })}`, { signal });

  if (!response.ok) {
    throw new ApiError(`Suggestions fetch failed: ${response.status}`, response.status);
  }

  const data = await response.json();
  return Array.isArray(data.suggestions) ? (data.suggestions as Suggestion[]) : [];
};
