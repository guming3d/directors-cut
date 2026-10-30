import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Director's Cut — Foundry Video Studio",
  description:
    "Turn a storyboard into a finished multi-scene video with AI agents on Microsoft Foundry.",
  // Tab and home-screen icons come from app/favicon.ico, app/icon.png, and
  // app/apple-icon.png (Next.js file conventions), generated from images/logo.png
  // by scripts/make_logo_assets.py.
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
