import type { Metadata } from "next"
import "./globals.css"
import { IdentityProvider } from "@/components/agent/providers/identity-provider"

export const metadata: Metadata = {
  title: "True Learning System UI",
  description: "React frontend scaffold for the True Learning agent experience.",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-CN" className="dark">
      <body>
        <IdentityProvider>{children}</IdentityProvider>
      </body>
    </html>
  )
}
