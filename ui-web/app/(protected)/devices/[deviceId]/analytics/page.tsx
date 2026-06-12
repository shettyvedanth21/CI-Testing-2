"use client";
import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

export default function DeviceAnalyticsPage() {
  const { deviceId } = useParams<{ deviceId: string }>();
  const router = useRouter();

  useEffect(() => {
    if (deviceId) router.replace(`/analytics?device=${deviceId}`);
  }, [deviceId, router]);

  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 64 }}>
      <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-500 mx-auto" />
    </div>
  );
}
