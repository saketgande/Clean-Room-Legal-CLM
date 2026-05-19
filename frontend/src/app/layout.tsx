import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { QueryProvider } from "@/lib/query";
import { LayoutProvider } from "@/lib/layout";
import { ToastProvider } from "@/components/toast";

export const metadata: Metadata = {
  title: "AEGIS — Legal CLM",
  description: "Contract-first legal AI and contract lifecycle management.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;600;700&family=Newsreader:opsz,wght@6..72,380;6..72,440;6..72,500;6..72,560&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <QueryProvider>
          <AuthProvider>
            <LayoutProvider>
              <ToastProvider>{children}</ToastProvider>
            </LayoutProvider>
          </AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
