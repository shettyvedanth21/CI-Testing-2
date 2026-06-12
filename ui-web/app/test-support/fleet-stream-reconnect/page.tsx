"use client";

import { useEffect, useState } from "react";

import { connectFleetStream, type FleetStreamEventData } from "@/lib/deviceApi";

export default function FleetStreamReconnectHarnessPage() {
  const [loadCount] = useState(1);
  const [openCount, setOpenCount] = useState(0);
  const [eventVersion, setEventVersion] = useState("0");
  const [errorCount, setErrorCount] = useState(0);

  useEffect(() => {
    const stopStream = connectFleetStream({
      pageSize: 10,
      onOpen: () => {
        setOpenCount((count) => count + 1);
      },
      onError: () => {
        setErrorCount((count) => count + 1);
      },
      onEvent: (payload: FleetStreamEventData) => {
        if (payload.event === "fleet_update") {
          setEventVersion(String(payload.version ?? 0));
        }
      },
    });

    return () => {
      stopStream();
    };
  }, []);

  return (
    <main>
      <div data-testid="harness-load-count">{loadCount}</div>
      <div data-testid="harness-open-count">{openCount}</div>
      <div data-testid="harness-event-version">{eventVersion}</div>
      <div data-testid="harness-error-count">{errorCount}</div>
    </main>
  );
}
