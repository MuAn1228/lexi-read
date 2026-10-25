import { useEffect, useState } from 'react';

const query = '(prefers-reduced-motion: reduce)';

/** Keep GIF selection in sync with a live system preference, including the first render. */
export function useMascotReducedMotion() {
  const [reduced, setReduced] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return reduced;
}
