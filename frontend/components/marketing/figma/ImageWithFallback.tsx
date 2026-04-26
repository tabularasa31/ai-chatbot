'use client'

import React, { useState } from 'react'
import Image from 'next/image'

const ERROR_IMG_SRC =
  'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iODgiIGhlaWdodD0iODgiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgc3Ryb2tlPSIjMDAwIiBzdHJva2UtbGluZWpvaW49InJvdW5kIiBvcGFjaXR5PSIuMyIgZmlsbD0ibm9uZSIgc3Ryb2tlLXdpZHRoPSIzLjciPjxyZWN0IHg9IjE2IiB5PSIxNiIgd2lkdGg9IjU2IiBoZWlnaHQ9IjU2IiByeD0iNiIvPjxwYXRoIGQ9Im0xNiA1OCAxNi0xOCAzMiAzMiIvPjxjaXJjbGUgY3g9IjUzIiBjeT0iMzUiIHI9IjciLz48L3N2Zz4KCg=='

interface ImageWithFallbackProps extends React.ImgHTMLAttributes<HTMLImageElement> {
  src: string
  alt: string
}

export function ImageWithFallback({ src, alt, className, style, ...rest }: ImageWithFallbackProps) {
  const [didError, setDidError] = useState(false)

  const handleError = () => {
    setDidError(true)
  }

  if (didError) {
    return (
      <div
        className={`inline-block bg-gray-100 text-center align-middle ${className ?? ''}`}
        style={style}
      >
        <div className="flex items-center justify-center w-full h-full">
          {/* eslint-disable-next-line @next/next/no-img-element -- data: URLs require img */}
          <img 
            src={ERROR_IMG_SRC} 
            alt="Error loading"
            data-original-url={src}
            {...rest}
          />
        </div>
      </div>
    )
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
