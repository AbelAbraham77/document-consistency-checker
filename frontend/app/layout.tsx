import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Crosscheck · Document consistency",
  description: "Review document inconsistencies with the original statements and source context.",
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
