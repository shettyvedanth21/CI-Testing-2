"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { authApi, type PlantProfile } from "@/lib/authApi";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";

interface CreatePlantModalProps {
  tenantId: string;
  isOpen: boolean;
  onClose: () => void;
  initialPlant?: PlantProfile | null;
  onSuccess: (plant: PlantProfile) => void;
}

const TIMEZONE_OPTIONS = [
  { value: "Asia/Kolkata", label: "Asia/Kolkata (IST)" },
  { value: "Asia/Dubai", label: "Asia/Dubai (GST)" },
  { value: "Asia/Singapore", label: "Asia/Singapore (SGT)" },
  { value: "Asia/Tokyo", label: "Asia/Tokyo (JST)" },
  { value: "Europe/London", label: "Europe/London (GMT/BST)" },
  { value: "Europe/Berlin", label: "Europe/Berlin (CET/CEST)" },
  { value: "America/New_York", label: "America/New_York (EST/EDT)" },
  { value: "America/Los_Angeles", label: "America/Los_Angeles (PST/PDT)" },
  { value: "America/Chicago", label: "America/Chicago (CST/CDT)" },
  { value: "UTC", label: "UTC" },
];

export function CreatePlantModal({ tenantId, isOpen, onClose, initialPlant = null, onSuccess }: CreatePlantModalProps) {
  const titleId = useId();
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [timezone, setTimezone] = useState("Asia/Kolkata");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setName("");
      setLocation("");
      setTimezone("Asia/Kolkata");
      setError(null);
      setIsSubmitting(false);
      return;
    }
    setName(initialPlant?.name ?? "");
    setLocation(initialPlant?.location ?? "");
    setTimezone(initialPlant?.timezone ?? "Asia/Kolkata");
    setError(null);
    setIsSubmitting(false);
  }, [initialPlant, isOpen]);

  const isEditMode = Boolean(initialPlant);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    if (name.trim().length < 2) {
      setError("Plant name must be at least 2 characters.");
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const plant = isEditMode
        ? await authApi.updatePlant(tenantId, initialPlant!.id, {
            name: name.trim(),
            location: location.trim() || undefined,
            timezone,
          })
        : await authApi.createPlant(tenantId, {
            name: name.trim(),
            location: location.trim() || undefined,
            timezone,
          });
      onSuccess(plant);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${isEditMode ? "update" : "create"} plant`);
    } finally {
      setIsSubmitting(false);
    }
  }

  const dialogTitle = isEditMode ? "Edit plant" : "Add plant";
  const dialogDescription = isEditMode
    ? "Update the saved name, location, or timezone for this plant."
    : "Register a plant, site, or factory building for this organisation.";
  const submitLabel = isEditMode ? "Save changes" : "Create plant";

  if (!isOpen) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button
        type="button"
        aria-label="Close create plant modal"
        className="absolute inset-0"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 w-full max-w-lg rounded-[1.5rem] border border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-[var(--shadow-raised)]"
      >
        <div className="border-b border-[var(--border-subtle)] px-5 py-4">
          <h2 id={titleId} className="text-lg font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            {dialogTitle}
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            {dialogDescription}
          </p>
        </div>

        <form className="space-y-4 p-5" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            label="Plant name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Pune Factory"
            minLength={2}
            required
            disabled={isSubmitting}
          />
          <Input
            label="Location"
            value={location}
            onChange={(event) => setLocation(event.target.value)}
            placeholder="Pune, Maharashtra"
            disabled={isSubmitting}
          />
          <Select
            label="Timezone"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            options={TIMEZONE_OPTIONS}
            disabled={isSubmitting}
          />

          {error ? (
            <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-3 py-2 text-sm text-[var(--tone-danger-text)]">
              {error}
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button type="submit" isLoading={isSubmitting} disabled={isSubmitting}>
              {submitLabel}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
