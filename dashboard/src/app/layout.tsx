import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Spraylogic · Sensor Dashboard',
  description: 'NIR Multispectral Flight Visualization',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body>{children}</body>
    </html>
  )
}
