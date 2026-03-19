'use client'

import { useRef, useState, useEffect } from 'react'

/**
 * Returns [ref, isInView] for scroll-triggered animations.
 * Use ref on the element and isInView for motion animate prop.
 */
export function useScrollAnimation(): [
  React.RefObject<HTMLDivElement>,
  boolean,
] {
  const ref = useRef<HTMLDivElement | null>(null)
  const [isInView, setIsInView] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const observer = new IntersectionObserver(
      ([entry]) => setIsInView(entry.isIntersecting),
      { threshold: 0.1 }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // Cast for framer-motion compatibility (ref.current can be null before mount)
  return [ref as React.RefObject<HTMLDivElement>, isInView]
}
