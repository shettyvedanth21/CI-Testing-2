"use client";

import { useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { deleteDevice } from "@/lib/deviceApi";

interface DeleteDeviceDialogProps {
  isOpen: boolean;
  deviceId: string;
  deviceName: string;
  onClose: () => void;
  onSuccess: (deletedDeviceId: string) => void;
}

export function DeleteDeviceDialog({
  isOpen,
  deviceId,
  deviceName,
  onClose,
  onSuccess,
}: DeleteDeviceDialogProps) {
  const titleId = useId();
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) {
      setIsDeleting(false);
      setError(null);
    }
  }, [isOpen]);

  async function handleDelete() {
    setIsDeleting(true);
    setError(null);
    try {
      await deleteDevice(deviceId);
      onSuccess(deviceId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete device");
      setIsDeleting(false);
    }
  }

  if (!isOpen) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button
        type="button"
        aria-label="Close delete device dialog"
        className="absolute inset-0"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 w-full max-w-[420px] rounded-[1.5rem] border border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-[var(--shadow-raised)]"
      >
        <div className="border-b border-[var(--border-subtle)] px-5 py-4">
          <h2 id={titleId} className="text-lg font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            Delete Device
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Are you sure you want to delete this device?
          </p>
        </div>

        <div className="space-y-4 p-5">
          <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-3">
            <p className="text-base font-semibold text-[var(--text-primary)]">{deviceName}</p>
            <p className="mt-1 font-mono text-xs text-[var(--text-secondary)]">{deviceId}</p>
          </div>

          <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            This action cannot be undone. All historical data, rules, and configuration for this device will be permanently removed.
          </div>

          {error ? (
            <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-3 py-2 text-sm text-[var(--tone-danger-text)]">
              {error}
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={isDeleting}>
              Cancel
            </Button>
            <Button type="button" variant="danger" onClick={() => void handleDelete()} disabled={isDeleting}>
              {isDeleting ? "Deleting..." : "Delete Device"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
