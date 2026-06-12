import type { ReactNode } from "react";
import { SuperAdminOrgGate } from "@/components/SuperAdminOrgGate";
import { FeatureGate } from "@/components/auth/FeatureGate";

export default function ReportsLayout({ children }: { children: ReactNode }) {
  return (
    <SuperAdminOrgGate>
      <FeatureGate feature="reports">{children}</FeatureGate>
    </SuperAdminOrgGate>
  );
}
