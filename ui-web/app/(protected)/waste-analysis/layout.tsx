import type { ReactNode } from "react";
import { SuperAdminOrgGate } from "@/components/SuperAdminOrgGate";
import { FeatureGate } from "@/components/auth/FeatureGate";

export default function WasteAnalysisLayout({ children }: { children: ReactNode }) {
  return (
    <SuperAdminOrgGate>
      <FeatureGate feature="waste_analysis">{children}</FeatureGate>
    </SuperAdminOrgGate>
  );
}
