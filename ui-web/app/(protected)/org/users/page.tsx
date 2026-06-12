"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/authContext";
import { authApi, type PlantProfile, type UserProfile } from "@/lib/authApi";
import { PageHeader, SectionCard } from "@/components/ui/page-scaffold";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { RoleBadge } from "@/components/auth/RoleBadge";
import { Badge } from "@/components/ui/badge";
import { InviteUserModal } from "@/components/auth/InviteUserModal";
import { EditUserModal } from "@/components/auth/EditUserModal";
import { OrgFeatureAccessEditor } from "@/components/auth/OrgFeatureAccessEditor";
import { resolveVisiblePlants } from "@/lib/orgScope";
import { getLifecycleActions, getLifecycleStatus } from "@/lib/userLifecycle";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatIST, getRelativeTime } from "@/lib/utils";

type UserPlantAccessMap = Record<string, string[]>;

function initials(name: string | null, email: string): string {
  const source = (name || email).trim();
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}

function UsersSkeleton() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>User</TableHead>
          <TableHead>Role</TableHead>
          <TableHead>Plants</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Last login</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {Array.from({ length: 4 }).map((_, index) => (
          <TableRow key={index} className="animate-pulse">
            <TableCell><div className="h-10 w-48 rounded bg-[var(--surface-2)]" /></TableCell>
            <TableCell><div className="h-6 w-24 rounded-full bg-[var(--surface-2)]" /></TableCell>
            <TableCell><div className="h-4 w-20 rounded bg-[var(--surface-2)]" /></TableCell>
            <TableCell><div className="h-6 w-24 rounded-full bg-[var(--surface-2)]" /></TableCell>
            <TableCell><div className="h-4 w-24 rounded bg-[var(--surface-2)]" /></TableCell>
            <TableCell><div className="ml-auto h-8 w-28 rounded bg-[var(--surface-2)]" /></TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function UsersMobileSkeleton({ index }: { index: number }) {
  return (
    <div key={`user-mobile-skeleton-${index}`} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-0)] p-4 animate-pulse">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-full bg-[var(--surface-2)]" />
        <div className="min-w-0 flex-1">
          <div className="h-4 w-36 rounded bg-[var(--surface-2)]" />
          <div className="mt-2 h-3 w-48 rounded bg-[var(--surface-2)]" />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="h-16 rounded-xl bg-[var(--surface-2)]" />
        <div className="h-16 rounded-xl bg-[var(--surface-2)]" />
        <div className="h-16 rounded-xl bg-[var(--surface-2)]" />
        <div className="h-16 rounded-xl bg-[var(--surface-2)]" />
      </div>
      <div className="mt-4 h-10 rounded-xl bg-[var(--surface-2)]" />
    </div>
  );
}

function UserStatusBadge({ label }: { label: string }) {
  if (label === "Active") {
    return (
      <Badge variant="success">
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          Active
        </span>
      </Badge>
    );
  }
  if (label === "Invited") {
    return (
      <Badge variant="default">
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
          Invited
        </span>
      </Badge>
    );
  }
  if (label === "Invite expired") {
    return (
      <Badge variant="warning">
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
          Invite expired
        </span>
      </Badge>
    );
  }
  return (
    <Badge variant="default">
      <span className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
        Deactivated
      </span>
    </Badge>
  );
}

function UserMobileCard({
  user,
  plantLabel,
  relative,
  confirmingUserId,
  processingDeactivateId,
  onEdit,
  onResendOrReinvite,
  onReactivate,
  onConfirmDeactivate,
  onCancelDeactivate,
  onDeactivate,
}: {
  user: UserProfile;
  plantLabel: string;
  relative: string;
  confirmingUserId: string | null;
  processingDeactivateId: string | null;
  onEdit: (user: UserProfile) => void;
  onResendOrReinvite: (user: UserProfile) => void;
  onReactivate: (userId: string) => void;
  onConfirmDeactivate: (userId: string) => void;
  onCancelDeactivate: () => void;
  onDeactivate: (userId: string) => void;
}) {
  const status = getLifecycleStatus(user);
  const actions = getLifecycleActions(user);

  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-0)] p-4">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--surface-2)] text-sm font-semibold text-[var(--text-primary)]">
          {initials(user.full_name, user.email)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-medium text-[var(--text-primary)]">{user.full_name || "Unnamed user"}</div>
          <div className="mt-0.5 break-all text-xs text-[var(--text-secondary)]">{user.email}</div>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
          <dt className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">Role</dt>
          <dd className="mt-1"><RoleBadge role={user.role} size="sm" /></dd>
        </div>
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
          <dt className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">Plants</dt>
          <dd className="mt-1 text-sm text-[var(--text-primary)]">{plantLabel}</dd>
        </div>
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
          <dt className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">Status</dt>
          <dd className="mt-1"><UserStatusBadge label={status.label} /></dd>
        </div>
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
          <dt className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">Last login</dt>
          <dd className="mt-1 text-sm text-[var(--text-primary)]">
            {user.last_login_at ? (relative || formatIST(user.last_login_at, "Never")) : "Never"}
          </dd>
        </div>
      </dl>

      <div className="mt-4">
        {confirmingUserId === user.id ? (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
            <p className="text-sm text-amber-900">Confirm deactivation for this user?</p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <Button
                variant="danger"
                className="w-full sm:w-auto"
                isLoading={processingDeactivateId === user.id}
                disabled={processingDeactivateId === user.id}
                onClick={() => onDeactivate(user.id)}
              >
                Yes, deactivate
              </Button>
              <Button
                variant="ghost"
                className="w-full sm:w-auto"
                disabled={processingDeactivateId === user.id}
                onClick={onCancelDeactivate}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            {user.role !== "org_admin" && actions.includes("deactivate") ? (
              <Button size="sm" variant="outline" className="w-full sm:w-auto" onClick={() => onEdit(user)}>
                Edit
              </Button>
            ) : null}
            {actions.includes("resend_invite") || actions.includes("reinvite") ? (
              <Button size="sm" variant="outline" className="w-full sm:w-auto" onClick={() => onResendOrReinvite(user)}>
                {actions.includes("resend_invite") ? "Resend invite" : "Reinvite"}
              </Button>
            ) : null}
            {actions.includes("reactivate") ? (
              <Button size="sm" variant="outline" className="w-full sm:w-auto" onClick={() => onReactivate(user.id)}>
                Reactivate
              </Button>
            ) : null}
            {actions.includes("deactivate") ? (
              <Button size="sm" variant="danger" className="w-full sm:w-auto" onClick={() => onConfirmDeactivate(user.id)}>
                Deactivate
              </Button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

export default function OrgUsersPage() {
  const { me, isLoading: isAuthLoading } = useAuth();
  const router = useRouter();
  const orgId = me?.tenant?.id ?? null;
  const orgName = me?.tenant?.name ?? null;
  const currentRole = me?.user.role ?? null;
  const isPlantManager = currentRole === "plant_manager";

  const [users, setUsers] = useState<UserProfile[]>([]);
  const [plants, setPlants] = useState<PlantProfile[]>([]);
  const [userPlantAccess, setUserPlantAccess] = useState<UserPlantAccessMap>({});
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<UserProfile | null>(null);
  const [confirmingUserId, setConfirmingUserId] = useState<string | null>(null);
  const [processingDeactivateId, setProcessingDeactivateId] = useState<string | null>(null);
  const accessiblePlants = useMemo(() => resolveVisiblePlants(me, plants), [me, plants]);
  const activeAccessiblePlants = useMemo(() => accessiblePlants.filter((plant) => plant.is_active), [accessiblePlants]);

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timer = window.setTimeout(() => setToast(null), 3000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!isAuthLoading && !orgId) {
      if (currentRole === "super_admin") {
        setError("Super admins should manage organisation teams from the Admin panel.");
        setIsLoading(false);
      } else {
        router.replace("/machines");
      }
      return;
    }

    if (!orgId) {
      return;
    }

    const resolvedOrgId = orgId;
    let active = true;
    async function load(): Promise<void> {
      setIsLoading(true);
      setError(null);

      try {
        const plantRows = await authApi.listPlants(resolvedOrgId);

        if (isPlantManager) {
          if (active) {
            setUsers([]);
            setPlants(plantRows);
            setUserPlantAccess({});
          }
          return;
        }

        const userRows = await authApi.listTenantUsers(resolvedOrgId);
        const plantAccessEntries = await Promise.all(
          userRows.map(async (user) => {
            if (user.role === "org_admin") {
              return [user.id, plantRows.map((plant) => plant.id)] as const;
            }
            const plantIds = await authApi.getUserPlantIds(resolvedOrgId, user.id);
            return [user.id, plantIds] as const;
          }),
        );

        if (!active) {
          return;
        }

        setUsers(userRows);
        setPlants(plantRows);
        setUserPlantAccess(Object.fromEntries(plantAccessEntries));
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to load users");
        }
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [currentRole, isAuthLoading, isPlantManager, orgId, router]);

  const activeUsers = useMemo(() => users.filter((user) => user.is_active).length, [users]);

  async function deactivateUser(userId: string): Promise<void> {
    if (!orgId) return;
    setProcessingDeactivateId(userId);
    try {
      await authApi.deactivateUser(orgId, userId);
      setUsers((current) =>
        current.map((user) => (user.id === userId ? { ...user, is_active: false } : user)),
      );
      setToast("User deactivated. Their session has been ended.");
      setConfirmingUserId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to deactivate user");
    } finally {
      setProcessingDeactivateId(null);
    }
  }

  async function resendOrReinviteUser(user: UserProfile): Promise<void> {
    if (!orgId) return;
    try {
      await authApi.resendInvitation(orgId, user.id);
      const refreshed = await authApi.listTenantUsers(orgId);
      setUsers(refreshed);
      setToast(user.invite_status === "pending" ? "Invite resent." : "New invite issued.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resend invite");
    }
  }

  async function reactivateUser(userId: string): Promise<void> {
    if (!orgId) return;
    try {
      await authApi.reactivateUser(orgId, userId);
      const refreshed = await authApi.listTenantUsers(orgId);
      setUsers(refreshed);
      setToast("User reactivated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reactivate user");
    }
  }

  return (
    <>
      <div className="space-y-5">
        <PageHeader
          title="Team"
          subtitle={
            isPlantManager
              ? "Invite operators and viewers to the plants assigned to you."
              : orgName
                ? `You are managing: ${orgName}`
                : "Manage users and plant-scoped access for your organisation."
          }
          actions={orgId && !isPlantManager ? (
            <Button className="w-full sm:w-auto" onClick={() => setInviteOpen(true)} disabled={isPlantManager && accessiblePlants.length === 0}>
              Invite User
            </Button>
          ) : undefined}
        />

        {orgId && currentRole === "org_admin" ? <OrgFeatureAccessEditor tenantId={orgId} mode="plant_manager" /> : null}
        {currentRole === "super_admin" ? (
          <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-3 text-sm text-[var(--text-secondary)]">
            Super admins should manage premium grants from the Admin organisation detail page. This page is reserved for org-level user and plant access.
          </div>
        ) : null}

        {toast ? (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
            {toast}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-4 py-3 text-sm text-[var(--tone-danger-text)]">
            {error}
          </div>
        ) : null}

        {isPlantManager ? (
          <SectionCard
            title="Invite users"
            subtitle={
              accessiblePlants.length > 0
                ? `${activeAccessiblePlants.length} active assigned plant${activeAccessiblePlants.length === 1 ? "" : "s"} available for invite scoping.`
                : "No plants are assigned to your account yet."
            }
          >
            {activeAccessiblePlants.length > 0 ? (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  {activeAccessiblePlants.map((plant) => (
                    <span
                      key={plant.id}
                      className="inline-flex items-center rounded-full border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-1 text-sm text-[var(--text-primary)]"
                    >
                      {plant.name}
                    </span>
                  ))}
                </div>
                <p className="text-sm text-[var(--text-secondary)]">
                  Plant managers can invite operators and viewers only, and each invite must be bound to exactly one of the plants shown above.
                </p>
                <Button className="w-full sm:w-auto" onClick={() => setInviteOpen(true)}>Invite User</Button>
              </div>
            ) : (
              <EmptyState message="No active plants are assigned to your account yet. Ask an org admin to assign or reactivate a plant before inviting users." />
            )}
          </SectionCard>
        ) : (
          <SectionCard
            title="Users"
            subtitle={`${activeUsers} active user${activeUsers === 1 ? "" : "s"} with plant-scoped access and alerts coverage.`}
          >
            {isLoading ? (
              <>
                <div className="space-y-3 md:hidden">
                  {Array.from({ length: 4 }).map((_, index) => (
                    <UsersMobileSkeleton key={index} index={index} />
                  ))}
                </div>
                <div className="hidden md:block">
                  <UsersSkeleton />
                </div>
              </>
            ) : users.length === 0 ? (
              <EmptyState message="No team members yet. Invite your first user to get started." />
            ) : (
              <>
                <div className="space-y-3 md:hidden">
                  {users.map((user) => {
                    const plantIds = userPlantAccess[user.id] ?? [];
                    const plantLabel = user.role === "org_admin"
                      ? "All plants"
                      : `${plantIds.length} plant${plantIds.length === 1 ? "" : "s"}`;
                    const relative = user.last_login_at ? getRelativeTime(user.last_login_at).replace(/[()]/g, "") : "";

                    return (
                      <UserMobileCard
                        key={user.id}
                        user={user}
                        plantLabel={plantLabel}
                        relative={relative}
                        confirmingUserId={confirmingUserId}
                        processingDeactivateId={processingDeactivateId}
                        onEdit={(nextUser) => setEditingUser(nextUser)}
                        onResendOrReinvite={(nextUser) => void resendOrReinviteUser(nextUser)}
                        onReactivate={(userId) => void reactivateUser(userId)}
                        onConfirmDeactivate={(userId) => setConfirmingUserId(userId)}
                        onCancelDeactivate={() => setConfirmingUserId(null)}
                        onDeactivate={(userId) => void deactivateUser(userId)}
                      />
                    );
                  })}
                </div>
                <div className="hidden md:block">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>User</TableHead>
                        <TableHead>Role</TableHead>
                        <TableHead>Plants</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Last login</TableHead>
                        <TableHead className="text-right">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {users.map((user) => {
                        const plantIds = userPlantAccess[user.id] ?? [];
                        const plantLabel = user.role === "org_admin"
                          ? "All plants"
                          : `${plantIds.length} plant${plantIds.length === 1 ? "" : "s"}`;
                        const relative = user.last_login_at ? getRelativeTime(user.last_login_at).replace(/[()]/g, "") : "";
                        const status = getLifecycleStatus(user);
                        const actions = getLifecycleActions(user);

                        return (
                          <TableRow key={user.id}>
                            <TableCell className="whitespace-normal">
                              <div className="flex items-center gap-3">
                                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--surface-2)] text-sm font-semibold text-[var(--text-primary)]">
                                  {initials(user.full_name, user.email)}
                                </div>
                                <div>
                                  <div className="font-medium">{user.full_name || "Unnamed user"}</div>
                                  <div className="mt-0.5 text-xs text-[var(--text-secondary)]">{user.email}</div>
                                </div>
                              </div>
                            </TableCell>
                            <TableCell>
                              <RoleBadge role={user.role} size="sm" />
                            </TableCell>
                            <TableCell>{plantLabel}</TableCell>
                            <TableCell>
                              <UserStatusBadge label={status.label} />
                            </TableCell>
                            <TableCell>
                              {user.last_login_at ? (relative || formatIST(user.last_login_at, "Never")) : "Never"}
                            </TableCell>
                            <TableCell className="text-right">
                              {confirmingUserId === user.id ? (
                                <div className="flex justify-end gap-2">
                                  <span className="self-center text-xs text-[var(--text-secondary)]">Confirm?</span>
                                  <Button
                                    size="sm"
                                    variant="danger"
                                    isLoading={processingDeactivateId === user.id}
                                    disabled={processingDeactivateId === user.id}
                                    onClick={() => void deactivateUser(user.id)}
                                  >
                                    Yes
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    disabled={processingDeactivateId === user.id}
                                    onClick={() => setConfirmingUserId(null)}
                                  >
                                    Cancel
                                  </Button>
                                </div>
                              ) : (
                                <div className="flex justify-end gap-2">
                                  {user.role !== "org_admin" && actions.includes("deactivate") ? (
                                    <Button size="sm" variant="outline" onClick={() => setEditingUser(user)}>
                                      Edit
                                    </Button>
                                  ) : null}
                                  {actions.includes("resend_invite") || actions.includes("reinvite") ? (
                                    <Button size="sm" variant="outline" onClick={() => void resendOrReinviteUser(user)}>
                                      {actions.includes("resend_invite") ? "Resend invite" : "Reinvite"}
                                    </Button>
                                  ) : null}
                                  {actions.includes("reactivate") ? (
                                    <Button size="sm" variant="outline" onClick={() => void reactivateUser(user.id)}>
                                      Reactivate
                                    </Button>
                                  ) : null}
                                  {actions.includes("deactivate") ? (
                                    <Button size="sm" variant="danger" onClick={() => setConfirmingUserId(user.id)}>
                                      Deactivate
                                    </Button>
                                  ) : null}
                                </div>
                              )}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </>
            )}
          </SectionCard>
        )}
      </div>

      {orgId ? (
        <InviteUserModal
          isOpen={inviteOpen}
          tenantId={orgId}
          callerRole={currentRole ?? "viewer"}
          availablePlants={activeAccessiblePlants}
          onClose={() => setInviteOpen(false)}
          onSuccess={(user, plantIds) => {
            setUsers((current) => {
              const existingIndex = current.findIndex((item) => item.id === user.id);
              if (existingIndex >= 0) {
                return current.map((item) => (item.id === user.id ? user : item));
              }
              return [user, ...current];
            });
            setUserPlantAccess((current) => ({ ...current, [user.id]: plantIds }));
          }}
        />
      ) : null}

      {orgId && editingUser ? (
        <EditUserModal
          isOpen={Boolean(editingUser)}
          tenantId={orgId}
          user={editingUser}
          currentPlantIds={userPlantAccess[editingUser.id] ?? []}
          availablePlants={plants.filter((plant) => plant.is_active)}
          onClose={() => setEditingUser(null)}
          onSuccess={(updated, plantIds) => {
            setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
            setUserPlantAccess((current) => ({ ...current, [updated.id]: plantIds }));
            setToast("User updated successfully.");
          }}
        />
      ) : null}
    </>
  );
}
