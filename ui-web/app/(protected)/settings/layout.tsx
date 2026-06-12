import type { ReactNode } from "react";
import { SuperAdminOrgGate } from "@/components/SuperAdminOrgGate";
import { FeatureGate } from "@/components/auth/FeatureGate";

export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <SuperAdminOrgGate>
      <FeatureGate feature="settings">{children}</FeatureGate>
    </SuperAdminOrgGate>
  );
}
