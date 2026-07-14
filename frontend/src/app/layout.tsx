import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { ClientErrorReporter } from "@/components/client-error-reporter";
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
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Apply the saved theme before first paint to avoid a flash. Mission
            Control is dark-first — a fresh visitor defaults to the navy ground. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('aegis-theme');var dark=t?t==='dark':true;document.documentElement.classList.toggle('dark',dark);}catch(e){document.documentElement.classList.add('dark');}})();",
          }}
        />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        {/* UI chrome is Segoe UI (Fluent stack, system font — no webfont needed).
            Newsreader remains for long-form contract reading surfaces only. */}
        <link
          href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,380;6..72,440;6..72,500;6..72,560&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <ClientErrorReporter />
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
