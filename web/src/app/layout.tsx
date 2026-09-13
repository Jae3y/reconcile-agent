import type { Metadata } from "next";
import { Toaster } from "sonner";

import "./globals.css";

export const metadata: Metadata = {
  title: "reconcile-agent · billing ↔ CRM reconciliation",
  description:
    "Live dashboard for an agent that reconciles Stripe billing against HubSpot CRM: five deterministic stages, one LLM judgement, every write verified by read-back.",
};

// Applied before paint so a dark-mode reload never flashes white.
const THEME_BOOT = `
(function () {
  try {
    var stored = localStorage.getItem("rc-theme");
    var dark = stored ? stored === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (dark) document.documentElement.classList.add("dark");
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="min-h-dvh antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:border focus:bg-[var(--bg-raised)] focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        {children}
        <Toaster
          position="bottom-right"
          closeButton
          toastOptions={{
            style: {
              background: "var(--bg-raised)",
              border: "1px solid var(--border)",
              color: "var(--text)",
            },
          }}
        />
      </body>
    </html>
  );
}
