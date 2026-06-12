import type { ReactNode } from "react";
import { SuperAdminOrgGate } from "@/components/SuperAdminOrgGate";

export default function DevicesLayout({ children }: { children: ReactNode }) {
  return <SuperAdminOrgGate>{children}</SuperAdminOrgGate>;
}
