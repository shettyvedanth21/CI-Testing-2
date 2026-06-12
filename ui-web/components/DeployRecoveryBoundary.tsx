"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DEPLOY_RECOVERY_STORAGE_KEY,
  extractDeployRecoveryMessage,
  isRecoverableDeployError,
  shouldAttemptAutomaticDeployRecovery,
} from "@/lib/deployRecovery";

type RecoveryBannerState = {
  mode: "reloading" | "manual";
  detail: string;
};

type RecoveryEventDetail = {
  action: "reload_scheduled" | "manual_required";
  detail: string;
};

declare global {
  interface Window {
    __factoryopsReloadOverride?: () => void;
  }
}

function readLastAttemptAt(): number | null {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = window.sessionStorage.getItem(DEPLOY_RECOVERY_STORAGE_KEY);
  if (!raw) {
    return null;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function writeLastAttemptAt(timestamp: number): void {
  window.sessionStorage.setItem(DEPLOY_RECOVERY_STORAGE_KEY, String(timestamp));
}

function emitRecoveryEvent(detail: RecoveryEventDetail): void {
  window.dispatchEvent(
    new CustomEvent<RecoveryEventDetail>("factoryops-deploy-recovery", { detail }),
  );
}

function reloadWindow(): void {
  if (typeof window.__factoryopsReloadOverride === "function") {
    window.__factoryopsReloadOverride();
    return;
  }
  window.location.reload();
}

export function DeployRecoveryBoundary() {
  const [banner, setBanner] = useState<RecoveryBannerState | null>(null);

  useEffect(() => {
    function handleRecoverableIssue(reason: unknown): void {
      if (!isRecoverableDeployError(reason)) {
        return;
      }

      const detail = extractDeployRecoveryMessage(reason).trim() || "The app version changed during this session.";
      const now = Date.now();
      const shouldAutoReload = shouldAttemptAutomaticDeployRecovery(readLastAttemptAt(), now);

      if (shouldAutoReload) {
        writeLastAttemptAt(now);
        setBanner({ mode: "reloading", detail });
        emitRecoveryEvent({ action: "reload_scheduled", detail });
        window.setTimeout(() => {
          reloadWindow();
        }, 60);
        return;
      }

      setBanner({ mode: "manual", detail });
      emitRecoveryEvent({ action: "manual_required", detail });
    }

    const onError = (event: ErrorEvent) => {
      handleRecoverableIssue(event.error ?? event.message);
    };

    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      handleRecoverableIssue(event.reason);
    };

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);

    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    };
  }, []);

  if (!banner) {
    return null;
  }

  return (
    <div
      data-testid="deploy-recovery-banner"
      className="pointer-events-none fixed inset-x-0 top-0 z-[120] flex justify-center px-4 pt-4"
    >
      <div className="pointer-events-auto flex w-full max-w-3xl items-center justify-between gap-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 shadow-lg">
        <div>
          <p className="font-semibold">
            {banner.mode === "reloading"
              ? "Shivex was updated. Reloading this tab..."
              : "Shivex was updated. This tab needs a reload."}
          </p>
          <p className="mt-1 text-xs text-amber-800">{banner.detail}</p>
        </div>
        {banner.mode === "manual" ? (
          <Button
            type="button"
            size="sm"
            onClick={() => {
              writeLastAttemptAt(Date.now());
              reloadWindow();
            }}
          >
            Reload now
          </Button>
        ) : null}
      </div>
    </div>
  );
}
