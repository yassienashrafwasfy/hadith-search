import { createContext } from 'react';

// The site is Arabic only; the context stays so components keep one way to read text.
export type Language = 'ar';

export interface LanguageContextValue {
  language: Language;
  t: (key: string, params?: Record<string, string | number>) => string;
  isRTL: boolean;
}

export const LanguageContext = createContext<LanguageContextValue | null>(null);
