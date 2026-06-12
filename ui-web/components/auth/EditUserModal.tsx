"use client";

import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { authApi, type PlantProfile, type UserProfile } from "@/lib/authApi";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface EditUserModalProps {
  isOpen: boolean;
  tenantId: string;
  user: UserProfile;
  currentPlantIds: string[];
  availablePlants: PlantProfile[];
  onClose: () => void;
  onSuccess: (updated: UserProfile, plantIds: string[]) => void;
}

type EditableRole = "plant_manager" | "operator" | "viewer";

const ROLE_OPTIONS: Array<{ value: EditableRole; label: string }> = [
  { value: "plant_manager", label: "Plant manager" },
  { value: "operator", label: "Operator" },
  { value: "viewer", label: "Viewer" },
];

export function EditUserModal({
  isOpen,
  tenantId,
  user,
  currentPlantIds,
  availablePlants,
  onClose,
  onSuccess,
}: EditUserModalProps) {
  const titleId = useId();
  const activePlants = useMemo(() => availablePlants.filter((plant) => plant.is_active), [availablePlants]);
  const [fullName, setFullName] = useState(user.full_name ?? "");
  const [role, setRole] = useState<EditableRole>((user.role === "plant_manager" || user.role === "operator" || user.role === "viewer") ? user.role : "viewer");
  const [plantIds, setPlantIds] = useState<string[]>(currentPlantIds);
  const [isActive, setIsActive] = useState(user.is_active);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setFullName(user.full_name ?? "");
      setRole((user.role === "plant_manager" || user.role === "operator" || user.role === "viewer") ? user.role : "viewer");
      setPlantIds(currentPlantIds.filter((plantId) => activePlants.some((plant) => plant.id === plantId)));
      setIsActive(user.is_active);
      setError(null);
      setIsSubmitting(false);
    }
  }, [activePlants, currentPlantIds, isOpen, user]);

  const hasChanges = useMemo(
    () =>
      fullName.trim() !== (user.full_name ?? "") ||
      role !== user.role ||
      isActive !== user.is_active ||
      plantIds.length !== currentPlantIds.length ||
      plantIds.some((id) => !currentPlantIds.includes(id)),
    [currentPlantIds, fullName, isActive, plantIds, role, user.full_name, user.is_active, user.role],
  );

  function togglePlant(plantId: string): void {
    setPlantIds((current) =>
      current.includes(plantId)
        ? current.filter((id) => id !== plantId)
        : [...current, plantId],
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    if (fullName.trim().length < 2) {
      setError("Full name must be at least 2 characters.");
      return;
    }
    if (plantIds.length === 0) {
      setError("Please select at least one plant");
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const updated = await authApi.updateUser(tenantId, user.id, {
        full_name: fullName.trim(),
        role,
        is_active: isActive,
        plant_ids: plantIds,
      });
      onSuccess(updated, plantIds);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update user");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (!isOpen) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button
        type="button"
        aria-label="Close edit user modal"
        className="absolute inset-0"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 w-full max-w-2xl rounded-[1.5rem] border border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-[var(--shadow-raised)]"
      >
        <div className="border-b border-[var(--border-subtle)] px-5 py-4">
          <h2 id={titleId} className="text-lg font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            Edit user
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Update user role, plant access, and active status for {user.email}.
          </p>
        </div>

        <form className="space-y-4 p-5" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            label="Full name"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            minLength={2}
            required
            disabled={isSubmitting}
          />

          <div className="space-y-1">
            <label className="block text-sm font-medium text-slate-700" htmlFor="edit-user-role">
              Role
            </label>
            <select
              id="edit-user-role"
              value={role}
              onChange={(event) => setRole(event.target.value as EditableRole)}
              disabled={isSubmitting}
              className="block h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 text-sm text-[var(--text-primary)] shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          {role !== user.role ? (
            <div className="rounded-2xl border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-700">
              Changing the role will require the user to log in again.
            </div>
          ) : null}

          <div className="space-y-3">
            <div>
              <p className="text-sm font-medium text-slate-700">Plant access</p>
              <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                Choose the plants this user can access.
              </p>
            </div>
            <div className="grid gap-2 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3 sm:grid-cols-2">
              {activePlants.map((plant) => (
                <label
                  key={plant.id}
                  className="flex items-center gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 py-2 text-sm text-[var(--text-primary)]"
                >
                  <input
                    type="checkbox"
                    checked={plantIds.includes(plant.id)}
                    onChange={() => togglePlant(plant.id)}
                    disabled={isSubmitting}
                    className="h-4 w-4 rounded border-[var(--border-subtle)] text-[var(--tone-info-solid)] focus:ring-[var(--focus-ring)]"
                  />
                  <span>{plant.name}</span>
                </label>
              ))}
            </div>
            {activePlants.length === 0 ? (
              <div className="rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                No active plants are available. Inactive plants cannot be used for new user assignments.
              </div>
            ) : null}
          </div>

          <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-3">
            <label className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-[var(--text-primary)]">User is active</p>
                <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                  Turn this off to revoke access immediately.
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={isActive}
                onClick={() => setIsActive((current) => !current)}
                className={`relative inline-flex h-7 w-12 items-center rounded-full transition ${isActive ? "bg-emerald-500" : "bg-slate-300"}`}
                disabled={isSubmitting}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white transition ${isActive ? "translate-x-6" : "translate-x-1"}`}
                />
              </button>
            </label>
          </div>

          {!isActive && user.is_active ? (
            <div className="rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              Deactivating will immediately end this user&apos;s session.
            </div>
          ) : null}

          {error ? (
            <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-3 py-2 text-sm text-[var(--tone-danger-text)]">
              {error}
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button type="submit" isLoading={isSubmitting} disabled={isSubmitting || !hasChanges}>
              Save Changes
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
