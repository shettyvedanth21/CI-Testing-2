import type { ReactNode } from "react";
import { AuthGuard } from "@/components/AuthGuard";
import { AppShell } from "@/components/layout/app-shell";

export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
