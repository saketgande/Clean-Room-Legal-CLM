"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
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
          background: "#F6F4EF",
          color: "#201C15",
          fontFamily:
            "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif",
          textAlign: "center",
          padding: "24px",
        }}
      >
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: 0 }}>
          The application crashed
        </h1>
        <p style={{ color: "#6E6557", marginTop: 12, maxWidth: 420 }}>
          A fatal error occurred. Reloading usually fixes it.
        </p>
        <button
          onClick={reset}
          style={{
            marginTop: 24,
            background: "#2F4A38",
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
          <p style={{ color: "#9D9483", marginTop: 24, fontSize: 12 }}>
            Reference: {error.digest}
          </p>
        )}
      </body>
    </html>
  );
}
