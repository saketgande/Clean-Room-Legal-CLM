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
        {/* Apply the theme before first paint to avoid a flash. An explicit
            saved choice ('aegis-theme') wins; otherwise follow the OS
            preference, and keep following it live until the user picks one. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var mq=window.matchMedia('(prefers-color-scheme: dark)');var apply=function(){var t=localStorage.getItem('aegis-theme');var dark=t?t==='dark':mq.matches;document.documentElement.classList.toggle('dark',dark);};apply();mq.addEventListener('change',function(){if(!localStorage.getItem('aegis-theme'))apply();});}catch(e){}})();",
          }}
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
