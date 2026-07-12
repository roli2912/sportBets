import type { Metadata } from "next";
import Link from "next/link";
import ComplianceFooter from "@/components/ComplianceFooter";
import "./globals.css";

export const metadata: Metadata = {
  title: "sportbets — betting analytics (dev)",
  description:
    "Europe-first multi-sport betting analytics. Educational and analytical only — never places bets.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="demo-banner">
          SYNTHETIC DEMO DATA — local development environment. Nothing here is a
          real price, pick or track record.
        </div>
        <header className="site">
          <div className="container">
            <Link href="/" className="brand">
              sport<span>bets</span>
            </Link>
            <nav>
              <Link href="/">+EV board</Link>
              <Link href="/track-record">Track record</Link>
              <Link href="/methodology">Methodology</Link>
            </nav>
            <span className="env">dev · UTC</span>
          </div>
        </header>
        <main>
          <div className="container">{children}</div>
        </main>
        <ComplianceFooter />
      </body>
    </html>
  );
}
