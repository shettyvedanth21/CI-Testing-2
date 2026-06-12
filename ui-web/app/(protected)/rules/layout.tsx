import type { ReactNode } from "react";
import { SuperAdminOrgGate } from "@/components/SuperAdminOrgGate";
import { FeatureGate } from "@/components/auth/FeatureGate";

export default function RulesLayout({ children }: { children: ReactNode }) {
  return (
    <SuperAdminOrgGate>
      <FeatureGate feature="rules">{children}</FeatureGate>
    </SuperAdminOrgGate>
  );
}
