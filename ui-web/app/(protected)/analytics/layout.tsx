import type { ReactNode } from "react";
import { SuperAdminOrgGate } from "@/components/SuperAdminOrgGate";
import { FeatureGate } from "@/components/auth/FeatureGate";

export default function AnalyticsLayout({ children }: { children: ReactNode }) {
  return (
    <SuperAdminOrgGate>
      <FeatureGate feature="analytics">{children}</FeatureGate>
    </SuperAdminOrgGate>
  );
}
