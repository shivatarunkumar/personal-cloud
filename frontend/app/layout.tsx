import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { THEME_COOKIE, type Theme } from "@/lib/theme";
import "./globals.css";

const APP_NAME = process.env.APP_NAME || "Personal Cloud";

export const metadata: Metadata = {
  title: { default: APP_NAME, template: `%s · ${APP_NAME}` },
  description: "Your private cloud drive: every file stored in your own Google Cloud bucket.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export const dynamic = "force-dynamic"; // theme cookie and app name are read per request

export default async function RootLayout({ children }: { children: ReactNode }) {
  // The chosen theme rides in a cookie so the server renders it on <html> itself: no
  // flash of the wrong palette. No cookie means "Auto".
  const saved = (await cookies()).get(THEME_COOKIE)?.value;
  const theme: Theme = saved === "dark" || saved === "light" ? saved : "system";
  return (
    <html lang="en" data-theme={theme === "system" ? undefined : theme}>
      <body className="font-sans antialiased">
        <AppShell appName={APP_NAME} theme={theme}>
          {children}
        </AppShell>
      </body>
    </html>
  );
}
