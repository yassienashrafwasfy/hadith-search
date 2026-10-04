import { Outlet } from 'react-router-dom';
import Navbar from '../components/Navbar';
import Footer from '../components/Footer';
import { useLanguage } from '../i18n/useLanguage';

const Layout = ({ mode, setMode }: { mode: string; setMode: (mode: string) => void }) => {
  const { t } = useLanguage();
  return (
    <div className="relative z-[1] flex flex-col min-h-[100dvh] bg-transparent text-on-background dark:text-dark-on-background font-body-main text-body-main antialiased">
      <a href="#content" className="skip-link font-ui-label text-ui-label">
        {t('a11y.skip')}
      </a>
      <Navbar mode={mode} onModeChange={setMode} />
      <div id="content" tabIndex={-1} className="flex flex-col flex-grow outline-none">
        <Outlet />
      </div>
      <Footer />
    </div>
  );
};

export default Layout;
