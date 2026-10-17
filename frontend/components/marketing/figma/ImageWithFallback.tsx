'use client'

import React, { useState } from 'react'
import Image from 'next/image'

interface ImageWithFallbackProps extends React.ImgHTMLAttributes<HTMLImageElement> {
  src: string
  alt: string
}

export function ImageWithFallback({ src, alt, className, style, ...rest }: ImageWithFallbackProps) {
  const [didError, setDidError] = useState(false)

  if (didError) return null

  const handleError = () => {
    setDidError(true)
  }

  // For local images or data URLs, use regular img tag
  if (src?.startsWith('data:') || src?.startsWith('/')) {
    return (
      /* eslint-disable-next-line @next/next/no-img-element -- data: and / paths require img */
      <img
        src={src}
        alt={alt}
        className={className}
        style={style}
        onError={handleError}
        {...rest}
      />
    )
  }

  // For external URLs, use Next Image (unoptimized for unknown dimensions)
  return (
    <Image
      src={src}
      alt={alt}
      className={className}
      style={style}
      onError={handleError}
      width={800}
      height={600}
      unoptimized
    />
  )
}
