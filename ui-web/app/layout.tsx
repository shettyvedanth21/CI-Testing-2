import type { Metadata, Viewport } from "next";
import "./globals.css";
import { DeployRecoveryBoundary } from "@/components/DeployRecoveryBoundary";
import { AuthProvider } from "@/lib/authContext";

export const metadata: Metadata = {
  title: "Shivex",
  description: "Industrial monitoring dashboard",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <DeployRecoveryBoundary />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
