import { useCallback, useEffect, type ReactNode } from 'react';
import { LanguageContext, type LanguageContextValue } from './LanguageContext';
import ar from './translations/ar';

interface LanguageProviderProps {
  children: ReactNode;
}

export const LanguageProvider = ({ children }: LanguageProviderProps) => {
  useEffect(() => {
    const html = document.documentElement;
    html.setAttribute('dir', 'rtl');
    html.setAttribute('lang', 'ar');
    html.classList.add('rtl');
  }, []);

  const t = useCallback((key: string, params?: Record<string, string | number>): string => {
    let value = ar[key] || key;
    if (params) {
      Object.entries(params).forEach(([paramKey, paramValue]) => {
        value = value.replace(new RegExp(`\\{${paramKey}\\}`, 'g'), String(paramValue));
      });
    }
    return value;
  }, []);

  const value: LanguageContextValue = { language: 'ar', t, isRTL: true };

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
};
