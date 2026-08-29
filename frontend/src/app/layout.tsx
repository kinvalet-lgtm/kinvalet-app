import type { Metadata } from "next";
import { Inter } from "next/font/google";
import dynamic from "next/dynamic";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

const Nav = dynamic(
  () => import("@/components/layout/Nav").then(m => ({ default: m.Nav })),
  { ssr: false }
);

export const metadata: Metadata = {
  title: "KinValet",
  description: "AI-powered family operations platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <Nav />
        <main className="pt-12">{children}</main>
      </body>
    </html>
  );
}
