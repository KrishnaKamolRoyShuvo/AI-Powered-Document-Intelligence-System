import "./globals.css";
import Link from "next/link";
import type { ReactNode } from "react";

export const metadata = {
  title: "Document Intelligence",
  description: "Invoice extraction, validation and search",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="nav">
          <strong>Document Intelligence</strong>
          <nav>
            <Link href="/">Documents</Link>
            <Link href="/search">Search</Link>
          </nav>
        </header>
        <main className="page">{children}</main>
      </body>
    </html>
  );
}
