"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useAuth } from "@/lib/authContext";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FEATURE_LABELS, type FeatureKey } from "@/lib/features";
import { useTenantStore } from "@/lib/tenantStore";

interface FeatureGateProps {
  feature: FeatureKey;
  children: ReactNode;
}

export function FeatureGate({ feature, children }: FeatureGateProps) {
  const { me, isLoading } = useAuth();
  const { selectedTenantId } = useTenantStore();

  if (isLoading) {
    return null;
  }

  if (me?.user.role === "super_admin" && selectedTenantId && !me.entitlements) {
    return null;
  }

  const available = me?.entitlements?.available_features ?? [];
  if (available.includes(feature)) {
    return <>{children}</>;
  }

  return <AccessDenied feature={feature} />;
}

function AccessDenied({ feature }: { feature: FeatureKey }) {
  return (
    <div className="mx-auto flex min-h-[55vh] w-full max-w-2xl items-center px-4 py-10">
      <Card className="w-full border-[var(--border-subtle)] shadow-[var(--shadow-raised)]">
        <CardHeader>
          <CardTitle>Feature not enabled</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-[var(--text-secondary)]">
            {FEATURE_LABELS[feature]} is not enabled for this organisation or role.
          </p>
          <p className="text-sm text-[var(--text-secondary)]">
            Ask a super admin to enable it for the organisation, or return to the default modules.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link href="/machines">
              <Button>Back to Machines</Button>
            </Link>
            <Link href="/admin/tenants">
              <Button variant="outline">Open Admin</Button>
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
