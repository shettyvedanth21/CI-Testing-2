"use client";

import { useEffect, useState } from "react";

type RecoveryEventDetail = {
  action: "reload_scheduled" | "manual_required";
  detail: string;
};

export default function DeployRecoveryHarnessPage() {
  const [reloadCount, setReloadCount] = useState(0);
  const [lastAction, setLastAction] = useState("idle");

  useEffect(() => {
    const onRecovery = (event: Event) => {
      const detail = (event as CustomEvent<RecoveryEventDetail>).detail;
      setLastAction(detail?.action ?? "unknown");
    };
    const onReload = () => {
      setReloadCount((count) => count + 1);
    };

    window.addEventListener("factoryops-deploy-recovery", onRecovery);
    window.addEventListener("factoryops-test-reload", onReload);
    return () => {
      window.removeEventListener("factoryops-deploy-recovery", onRecovery);
      window.removeEventListener("factoryops-test-reload", onReload);
    };
  }, []);

  return (
    <main className="space-y-4 p-6 pt-28">
      <button
        type="button"
        data-testid="trigger-rsc-deploy-error"
        onClick={() => {
          window.dispatchEvent(
            new ErrorEvent("error", {
              message: "Failed to fetch RSC payload. Falling back to browser navigation.",
            }),
          );
        }}
      >
        Trigger RSC deploy error
      </button>
      <button
        type="button"
        data-testid="trigger-server-action-error"
        onClick={() => {
          window.dispatchEvent(
            new ErrorEvent("error", {
              message: "Server action request failed after deploy boundary changed.",
            }),
          );
        }}
      >
        Trigger server action deploy error
      </button>
      <button
        type="button"
        data-testid="trigger-generic-error"
        onClick={() => {
          window.dispatchEvent(
            new ErrorEvent("error", {
              message: "A generic validation error occurred.",
            }),
          );
        }}
      >
        Trigger generic error
      </button>

      <div data-testid="deploy-recovery-reload-count">{reloadCount}</div>
      <div data-testid="deploy-recovery-last-action">{lastAction}</div>
    </main>
  );
}
