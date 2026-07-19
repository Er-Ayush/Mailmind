import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
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
  title: "MailMind",
  description: "Chat with your Gmail inbox — AI email agent",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-screen bg-zinc-950 text-zinc-100 font-sans">
        <nav className="border-b border-zinc-800 bg-zinc-900/60 backdrop-blur sticky top-0 z-10">
          <div className="mx-auto max-w-5xl px-4 h-14 flex items-center gap-6">
            <Link href="/" className="font-semibold text-lg tracking-tight">
              📬 MailMind
            </Link>
            <Link href="/" className="text-sm text-zinc-400 hover:text-zinc-100">
              Chat
            </Link>
            <Link href="/transactions" className="text-sm text-zinc-400 hover:text-zinc-100">
              Transactions
            </Link>
            <span className="ml-auto text-xs text-zinc-500">
              local · free-tier · human-approved sends
            </span>
          </div>
        </nav>
        <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
