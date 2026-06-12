"use client";

import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { authApi, type PlantProfile, type UserProfile, type UserRole } from "@/lib/authApi";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface InviteUserModalProps {
  isOpen: boolean;
  tenantId: string;
  callerRole: UserRole;
  availablePlants: PlantProfile[];
  onClose: () => void;
  onSuccess: (user: UserProfile, plantIds: string[]) => void;
}

type InviteRole = "plant_manager" | "operator" | "viewer";

const ROLE_OPTIONS_BY_CALLER: Record<UserRole, Array<{ value: InviteRole; label: string }>> = {
  super_admin: [
    { value: "plant_manager", label: "Plant manager — can manage devices and rules" },
    { value: "operator", label: "Operator — can view and acknowledge alerts" },
    { value: "viewer", label: "Viewer — read-only access" },
  ],
  org_admin: [
    { value: "plant_manager", label: "Plant manager — can manage devices and rules" },
    { value: "operator", label: "Operator — can view and acknowledge alerts" },
    { value: "viewer", label: "Viewer — read-only access" },
  ],
  plant_manager: [
    { value: "operator", label: "Operator — can view and acknowledge alerts" },
    { value: "viewer", label: "Viewer — read-only access" },
  ],
  operator: [
    { value: "viewer", label: "Viewer — read-only access" },
  ],
  viewer: [
    { value: "viewer", label: "Viewer — read-only access" },
  ],
};

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function InviteUserModal({
  isOpen,
  tenantId,
  callerRole,
  availablePlants,
  onClose,
  onSuccess,
}: InviteUserModalProps) {
  const titleId = useId();
  const activePlants = useMemo(() => availablePlants.filter((plant) => plant.is_active), [availablePlants]);
  const allowedRoles = ROLE_OPTIONS_BY_CALLER[callerRole] ?? ROLE_OPTIONS_BY_CALLER.viewer;
  const plantManagerMode = callerRole === "plant_manager";
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<InviteRole>(allowedRoles[0]?.value ?? "viewer");
  const [selectedPlantIds, setSelectedPlantIds] = useState<string[]>([]);
  const [selectedPlantId, setSelectedPlantId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const plantManagerSelectedPlant = useMemo(
    () => activePlants.find((plant) => plant.id === selectedPlantId) ?? null,
    [activePlants, selectedPlantId],
  );

  useEffect(() => {
    if (!isOpen) {
      setFullName("");
      setEmail("");
      setRole(allowedRoles[0]?.value ?? "viewer");
      setSelectedPlantIds([]);
      setSelectedPlantId("");
      setError(null);
      setIsSubmitting(false);
      return;
    }

    setRole(allowedRoles[0]?.value ?? "viewer");
    setError(null);
    setIsSubmitting(false);

    if (plantManagerMode) {
      const firstPlantId = activePlants[0]?.id ?? "";
      setSelectedPlantId(firstPlantId);
      setSelectedPlantIds(firstPlantId ? [firstPlantId] : []);
    } else {
      setSelectedPlantIds([]);
      setSelectedPlantId("");
    }
  }, [activePlants, allowedRoles, isOpen, plantManagerMode]);

  useEffect(() => {
    if (!plantManagerMode) {
      return;
    }

    if (!selectedPlantId && activePlants.length > 0) {
      const firstPlantId = activePlants[0].id;
      setSelectedPlantId(firstPlantId);
      setSelectedPlantIds([firstPlantId]);
      return;
    }

    if (selectedPlantId && !activePlants.some((plant) => plant.id === selectedPlantId)) {
      const fallbackPlantId = activePlants[0]?.id ?? "";
      setSelectedPlantId(fallbackPlantId);
      setSelectedPlantIds(fallbackPlantId ? [fallbackPlantId] : []);
      setError(null);
    }
  }, [activePlants, plantManagerMode, selectedPlantId]);

  function togglePlant(plantId: string): void {
    setSelectedPlantIds((current) =>
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
    if (!isValidEmail(email.trim())) {
      setError("Enter a valid email address.");
      return;
    }
    const plantIds = plantManagerMode
      ? selectedPlantId
        ? [selectedPlantId]
        : []
      : selectedPlantIds;

    if (plantManagerMode) {
      if (activePlants.length === 0) {
        setError("You do not have any assigned plants to invite users into.");
        return;
      }
      if (plantIds.length !== 1 || !activePlants.some((plant) => plant.id === plantIds[0])) {
        setError("Plant managers must choose exactly one of their assigned plants.");
        return;
      }
    } else if (plantIds.length === 0) {
      setError("Please select at least one plant.");
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const user = await authApi.inviteUser(tenantId, {
        email: email.trim(),
        full_name: fullName.trim(),
        role,
        tenant_id: tenantId,
        plant_ids: plantIds,
      });
      onSuccess(user, plantIds);
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to invite user";
      if (message.toLowerCase().includes("reactivate")) {
        setError("This user was previously active and is currently deactivated. Use Reactivate from the user table.");
      } else if (message.toLowerCase().includes("registered") || message.toLowerCase().includes("taken")) {
        setError("This email is already registered in the system.");
      } else if (message.toLowerCase().includes("cannot create org_admin") || message.toLowerCase().includes("forbidden")) {
        setError("You cannot create that role from this panel.");
      } else {
        setError(message);
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  if (!isOpen) {
    return null;
  }

  const plantInviteDisabled = plantManagerMode
    ? isSubmitting || activePlants.length === 0 || !selectedPlantId
    : isSubmitting || activePlants.length === 0 || selectedPlantIds.length === 0;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button
        type="button"
        aria-label="Close invite user modal"
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
            Invite user
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Send an email invite so the user can set their own password securely.
          </p>
        </div>

        <form className="space-y-4 p-5" onSubmit={(event) => void handleSubmit(event)}>
          <div className="grid gap-4 md:grid-cols-2">
            <Input
              label="Full name"
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
              placeholder="Asha Verma"
              minLength={2}
              required
              disabled={isSubmitting}
            />
            <Input
              label="Email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="operator@factory.com"
              required
              disabled={isSubmitting}
            />
          </div>

          <div className="space-y-1">
            <label className="block text-sm font-medium text-slate-700" htmlFor="invite-user-role">
              Role
            </label>
            <select
              id="invite-user-role"
              value={role}
              onChange={(event) => setRole(event.target.value as InviteRole)}
              disabled={isSubmitting}
              className="block h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 text-sm text-[var(--text-primary)] shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
            >
              {allowedRoles.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-3">
            <div>
              <p className="text-sm font-medium text-slate-700">Plant access</p>
              <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                {plantManagerMode
                  ? "Choose exactly one of your assigned plants for this invite."
                  : "Choose the plants this user can access."}
              </p>
            </div>

            {plantManagerMode ? (
              <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
                {activePlants.length === 0 ? (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800">
                    You do not have any active assigned plants yet. Ask an org admin to assign or reactivate a plant before inviting users.
                  </div>
                ) : (
                  <label className="block space-y-2">
                    <span className="text-sm font-medium text-[var(--text-primary)]">Plant</span>
                    <select
                      value={selectedPlantId}
                      onChange={(event) => {
                        const nextPlantId = event.target.value;
                        setSelectedPlantId(nextPlantId);
                        setSelectedPlantIds(nextPlantId ? [nextPlantId] : []);
                      }}
                      disabled={isSubmitting || activePlants.length === 1}
                      className="block h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 text-sm text-[var(--text-primary)] shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
                    >
                      <option value="">Select a plant</option>
                      {activePlants.map((plant) => (
                        <option key={plant.id} value={plant.id}>
                          {plant.name}
                        </option>
                      ))}
                    </select>
                    {plantManagerSelectedPlant ? (
                      <p className="text-xs text-[var(--text-tertiary)]">
                        Selected plant: <span className="font-medium text-[var(--text-secondary)]">{plantManagerSelectedPlant.name}</span>
                      </p>
                    ) : null}
                  </label>
                )}
              </div>
            ) : (
              <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
                {activePlants.length === 0 ? (
                  <p className="text-sm text-[var(--text-secondary)]">No active plants available yet. Inactive plants cannot be used for new invites.</p>
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2">
                    {activePlants.map((plant) => (
                      <label
                        key={plant.id}
                        className="flex items-center gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 py-2 text-sm text-[var(--text-primary)]"
                      >
                        <input
                          type="checkbox"
                          checked={selectedPlantIds.includes(plant.id)}
                          onChange={() => togglePlant(plant.id)}
                          disabled={isSubmitting}
                          className="h-4 w-4 rounded border-[var(--border-subtle)] text-[var(--tone-info-solid)] focus:ring-[var(--focus-ring)]"
                        />
                        <span>{plant.name}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {error ? (
            <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-3 py-2 text-sm text-[var(--tone-danger-text)]">
              {error}
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button type="submit" isLoading={isSubmitting} disabled={plantInviteDisabled}>
              Invite user
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
