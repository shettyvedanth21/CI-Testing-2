"use client";

import { Card, CardContent } from "@/components/ui/card";
import { FEATURE_LABELS, type FeatureKey } from "@/lib/features";

interface LockedPremiumCardProps {
  feature: FeatureKey;
  description: string;
}

export function LockedPremiumCard({ feature, description }: LockedPremiumCardProps) {
  return (
    <Card className="h-full border-dashed border-slate-300 bg-slate-50/50">
      <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-100">
          <svg className="h-5 w-5 text-slate-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
          </svg>
        </div>
        <div>
          <p className="font-semibold text-slate-700">{FEATURE_LABELS[feature]}</p>
          <p className="mt-1 text-sm text-slate-500">{description}</p>
        </div>
        <p className="text-xs text-slate-400">Contact your super admin to enable this premium feature.</p>
      </CardContent>
    </Card>
  );
}
