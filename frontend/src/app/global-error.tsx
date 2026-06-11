"use client";

import { useEffect } from "react";
import { reportClientError } from "@/lib/client-logger";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    reportClientError({
      kind: "global-error",
      message: error.message || "Fatal error",
      stack: error.stack,
    });
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "#08090B",
          color: "#ECEEF1",
          fontFamily:
            "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif",
          textAlign: "center",
          padding: "24px",
        }}
      >
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: 0 }}>
          The application crashed
        </h1>
        <p style={{ color: "#8A909A", marginTop: 12, maxWidth: 420 }}>
          A fatal error occurred. Reloading usually fixes it.
        </p>
        <button
          onClick={reset}
          style={{
            marginTop: 24,
            background: "#7C3AED",
            color: "#fff",
            border: 0,
            borderRadius: 8,
            padding: "10px 18px",
            fontSize: 14,
            cursor: "pointer",
          }}
        >
          Reload
        </button>
        {error.digest && (
          <p style={{ color: "#565B64", marginTop: 24, fontSize: 12 }}>
            Reference: {error.digest}
          </p>
        )}
      </body>
    </html>
  );
}
