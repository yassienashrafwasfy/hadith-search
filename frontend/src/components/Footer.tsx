import { useLanguage } from '../i18n/useLanguage';

const Footer = () => {
  const { t } = useLanguage();

  return (
    <footer className="mt-16 px-4 sm:px-6 lg:px-10 pb-10">
      <div className="max-w-[1280px] mx-auto flex flex-col gap-5">
        <div className="ornament text-secondary dark:text-dark-secondary" aria-hidden="true">
          <span className="khatam" />
        </div>
        <p className="font-ui-caption text-ui-caption text-on-surface-variant dark:text-dark-on-surface-variant text-center max-w-prose mx-auto">
          {t('footer.colophon')}
        </p>
      </div>
    </footer>
  );
};

export default Footer;
