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
        {/* Apply the saved theme before first paint to avoid a flash. The app is
            light-first — a fresh visitor defaults to the light ground. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('aegis-theme');var dark=t?t==='dark':false;document.documentElement.classList.toggle('dark',dark);}catch(e){}})();",
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
