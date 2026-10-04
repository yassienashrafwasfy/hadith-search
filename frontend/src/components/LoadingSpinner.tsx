// A small khatam (eight-point star) that turns slowly. Reduced-motion users get it still.
const LoadingSpinner = () => (
  <div className="relative w-12 h-12 flex items-center justify-center motion-safe:animate-spin-slow" aria-hidden="true">
    <svg className="w-full h-full text-secondary dark:text-dark-secondary" viewBox="0 0 100 100">
      <rect x="20" y="20" width="60" height="60" fill="none" stroke="currentColor" strokeWidth="4" />
      <rect x="20" y="20" width="60" height="60" fill="none" stroke="currentColor" strokeWidth="4" transform="rotate(45 50 50)" />
    </svg>
  </div>
);

export default LoadingSpinner;
