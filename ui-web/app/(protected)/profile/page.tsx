"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/authContext";
import { authApi, type PlantProfile, type UserRole } from "@/lib/authApi";
import { RoleBadge } from "@/components/auth/RoleBadge";
import { Button } from "@/components/ui/button";
import { formatIST, getRelativeTime } from "@/lib/utils";
import { cn } from "@/lib/utils";

const ROLE_ACCENTS: Record<UserRole, string> = {
  super_admin: "bg-red-600 text-white shadow-[0_10px_30px_rgba(220,38,38,0.25)]",
  org_admin: "bg-violet-600 text-white shadow-[0_10px_30px_rgba(124,58,237,0.25)]",
  plant_manager: "bg-emerald-600 text-white shadow-[0_10px_30px_rgba(16,185,129,0.25)]",
  operator: "bg-blue-600 text-white shadow-[0_10px_30px_rgba(37,99,235,0.25)]",
  viewer: "bg-slate-600 text-white shadow-[0_10px_30px_rgba(71,85,105,0.25)]",
};

function initials(fullName: string | null, email: string): string {
  const source = (fullName || email).trim();
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}

function displayDate(value: string | null): string {
  return value ? formatIST(value, "Unknown") : "Unknown";
}

export default function ProfilePage() {
  const { me, logout } = useAuth();
  const router = useRouter();
  const [plants, setPlants] = useState<PlantProfile[]>([]);
  const [isLoadingPlants, setIsLoadingPlants] = useState(false);
  const [isEditingName, setIsEditingName] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [isSavingName, setIsSavingName] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const role = me?.user.role ?? "viewer";
  const canEditOwnName = Boolean(me?.tenant?.id && role === "org_admin");
  const plantNamesById = useMemo(() => new Map(plants.map((plant) => [plant.id, plant.name])), [plants]);
  const visiblePlantNames = useMemo(
    () => (me?.plant_ids ?? []).map((plantId) => plantNamesById.get(plantId)).filter((name): name is string => Boolean(name)),
    [me?.plant_ids, plantNamesById],
  );

  useEffect(() => {
    if (!me?.tenant?.id) {
      return;
    }

    let active = true;
    setIsLoadingPlants(true);
    void authApi
      .listPlants(me.tenant.id)
      .then((plantRows) => {
        if (active) {
          setPlants(plantRows);
        }
      })
      .catch(() => {
        if (active) {
          setPlants([]);
        }
      })
      .finally(() => {
        if (active) {
          setIsLoadingPlants(false);
        }
      });

    return () => {
      active = false;
    };
  }, [me?.tenant?.id]);

  useEffect(() => {
    setDraftName(me?.user.full_name ?? "");
  }, [me?.user.full_name]);

  async function handleSaveName(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!me?.tenant?.id || !me?.user.id) {
      return;
    }

    const nextName = draftName.trim();
    if (nextName.length < 2) {
      setError("Full name must be at least 2 characters.");
      return;
    }

    setError(null);
    setIsSavingName(true);
    try {
      await authApi.updateUser(me.tenant.id, me.user.id, { full_name: nextName });
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update name");
    } finally {
      setIsSavingName(false);
    }
  }

  async function handleSignOut(): Promise<void> {
    await logout();
    router.push("/login");
  }

  if (!me) {
    return null;
  }

  return (
    <div className="mx-auto flex min-h-[calc(100vh-10rem)] max-w-md items-start justify-center py-4 sm:py-8">
      <div className="surface-panel w-full overflow-hidden rounded-[1.5rem]">
        <div className="px-6 py-6">
          <div className="flex flex-col items-center text-center">
            <div
              className={cn(
                "flex h-16 w-16 items-center justify-center rounded-full text-xl font-semibold tracking-[-0.03em]",
                ROLE_ACCENTS[role],
              )}
            >
              {initials(me.user.full_name, me.user.email)}
            </div>

            <div className="mt-4 space-y-1">
              {canEditOwnName ? (
                <form className="space-y-3" onSubmit={(event) => void handleSaveName(event)}>
                  <div className="flex items-center justify-center gap-2">
                    <input
                      value={draftName}
                      onChange={(event) => setDraftName(event.target.value)}
                      className="w-full max-w-[18rem] rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 py-2 text-center text-2xl font-semibold tracking-[-0.02em] text-[var(--text-primary)] shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
                      aria-label="Full name"
                    />
                  </div>
                  <div className="flex items-center justify-center gap-2">
                    <Button type="submit" size="sm" isLoading={isSavingName} disabled={isSavingName}>
                      Save
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => setDraftName(me.user.full_name ?? "")}
                      disabled={isSavingName}
                    >
                      Reset
                    </Button>
                  </div>
                </form>
              ) : (
                <h1 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
                  {me.user.full_name || "Unnamed user"}
                </h1>
              )}
              <p className="text-sm text-[var(--text-secondary)]">{me.user.email}</p>
            </div>

            <div className="mt-4">
              <RoleBadge role={role} size="md" />
            </div>
          </div>

          <div className="mt-6 space-y-4 border-t border-[var(--border-subtle)] pt-5">
            <div className="grid gap-3 text-sm">
              <div className="flex items-center justify-between gap-4">
                <span className="text-[var(--text-tertiary)]">Organisation</span>
                <span className="text-right font-medium text-[var(--text-primary)]">
                  {me.tenant?.name ?? "No tenant"}
                </span>
              </div>
              <div className="flex items-center justify-between gap-4">
                <span className="text-[var(--text-tertiary)]">Member since</span>
                <span className="text-right font-medium text-[var(--text-primary)]">
                  {displayDate(me.user.created_at)}
                </span>
              </div>
              <div className="flex items-center justify-between gap-4">
                <span className="text-[var(--text-tertiary)]">Last login</span>
                <span className="text-right font-medium text-[var(--text-primary)]">
                  {me.user.last_login_at ? `${formatIST(me.user.last_login_at)} ${getRelativeTime(me.user.last_login_at)}`.trim() : "Never"}
                </span>
              </div>
            </div>

            {error ? (
              <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-3 py-2 text-sm text-[var(--tone-danger-text)]">
                {error}
              </div>
            ) : null}

            <div className="border-t border-[var(--border-subtle)] pt-4">
              {role === "super_admin" ? (
                <div className="space-y-3">
                  <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                    Access level: All organisations
                  </div>
                  <Link
                    href="/admin/tenants"
                    className="inline-flex items-center justify-center rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-2 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[var(--surface-2)]"
                  >
                    Manage organisations →
                  </Link>
                </div>
              ) : role === "org_admin" ? (
                <div className="space-y-3">
                  <div className="rounded-2xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm text-violet-700">
                    Access level: Full access to {me.tenant?.name ?? "your tenant"}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Link
                      href="/tenant/users"
                      className="inline-flex items-center justify-center rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-2 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[var(--surface-2)]"
                    >
                      Manage team
                    </Link>
                    <Link
                      href="/tenant/plants"
                      className="inline-flex items-center justify-center rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-2 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[var(--surface-2)]"
                    >
                      Manage plants
                    </Link>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="rounded-2xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700">
                    {isLoadingPlants
                      ? "Loading your plants..."
                      : "Your plants"}
                  </div>
                  {!isLoadingPlants && visiblePlantNames.length > 0 ? (
                    <div className="flex flex-wrap gap-2">
                      {visiblePlantNames.map((plantName) => (
                        <span
                          key={plantName}
                          className="inline-flex items-center rounded-full border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-1 text-sm text-[var(--text-primary)]"
                        >
                          {plantName}
                        </span>
                      ))}
                    </div>
                  ) : !isLoadingPlants ? (
                    <p className="text-sm text-[var(--text-secondary)]">
                      You have not been assigned to any plants. Contact your org admin.
                    </p>
                  ) : null}
                </div>
              )}
            </div>

            <div className="border-t border-[var(--border-subtle)] pt-4">
              <Button variant="danger" className="w-full" onClick={() => void handleSignOut()}>
                Sign out
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
