'use client';

import { useLayoutEffect } from 'react';

export function ForcedLightScheme() {
  useLayoutEffect(() => {
    document.documentElement.classList.remove('dark');
  }, []);
  return <span hidden data-scheme-forced />;
}
