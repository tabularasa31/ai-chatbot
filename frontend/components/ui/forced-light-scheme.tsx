'use client';

import { useLayoutEffect } from 'react';

export function ForcedLightScheme() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.classList.remove('dark');
    return () => {
      root.classList.remove('light');
      root.style.removeProperty('color-scheme');
    };
  }, []);
  return <span hidden data-scheme-forced />;
}
