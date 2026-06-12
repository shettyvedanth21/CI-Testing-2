"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { authApi, type TenantProfile, type PlantProfile, type UserProfile } from "@/lib/authApi";
import { Button } from "@/components/ui/button";
import { PageHeader, SectionCard } from "@/components/ui/page-scaffold";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/EmptyState";
import { RoleBadge } from "@/components/auth/RoleBadge";
import { CreatePlantModal } from "@/components/auth/CreatePlantModal";
import { CreateOrgAdminModal } from "@/components/auth/CreateOrgAdminModal";
import { OrgFeatureAccessEditor } from "@/components/auth/OrgFeatureAccessEditor";
import { OrgHardwareTab } from "@/components/admin/OrgHardwareTab";
import { OrgNotificationUsageTab } from "@/components/admin/OrgNotificationUsageTab";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatIST, getRelativeTime } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { buildAdminOrgTabs } from "@/lib/hardwareAdmin";
import { setSelectedTenantId } from "@/lib/tenantStore";
import { useAuth } from "@/lib/authContext";
import { getLifecycleActions, getLifecycleStatus } from "@/lib/userLifecycle";

type TabKey = "plants" | "users" | "hardware" | "notification_usage";

function DataSkeletonTable({ columns }: { columns: string[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((column) => (
            <TableHead key={column}>{column}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {Array.from({ length: 3 }).map((_, index) => (
          <TableRow key={index} className="animate-pulse">
            {columns.map((column) => (
              <TableCell key={`${column}-${index}`}>
                <div className="h-4 w-28 rounded bg-[var(--surface-2)]" />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function AdminOrgDetailPage() {
  const { me } = useAuth();
  const isSuperAdmin = me?.user.role === "super_admin";
  const params = useParams<{ orgId?: string; tenantId?: string }>();
  const tenantId =
    typeof params.tenantId === "string" && params.tenantId
      ? params.tenantId
      : typeof params.orgId === "string"
        ? params.orgId
        : "";

  const [activeTab, setActiveTab] = useState<TabKey>("plants");
  const [org, setOrg] = useState<TenantProfile | null>(null);
  const [plants, setPlants] = useState<PlantProfile[]>([]);
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isCreatePlantOpen, setIsCreatePlantOpen] = useState(false);
  const [isCreateOrgAdminOpen, setIsCreateOrgAdminOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [deactivatingUserId, setDeactivatingUserId] = useState<string | null>(null);
  const [reactivatingUserId, setReactivatingUserId] = useState<string | null>(null);
  const [resendingInviteUserId, setResendingInviteUserId] = useState<string | null>(null);
  const [hardwareCount, setHardwareCount] = useState(0);
  const [updatingOrg, setUpdatingOrg] = useState(false);
  const [updatingPlantId, setUpdatingPlantId] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timer = window.setTimeout(() => setToast(null), 3000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (tenantId) {
      setSelectedTenantId(tenantId);
    }
  }, [tenantId]);

  useEffect(() => {
    let isMounted = true;

    async function load(): Promise<void> {
      setIsLoading(true);
      setError(null);
      try {
        const [orgs, plantRows, userRows] = await Promise.all([
          authApi.listTenants(),
          authApi.listPlants(tenantId),
          authApi.listTenantUsers(tenantId),
        ]);

        if (!isMounted) {
          return;
        }

        setOrg(orgs.find((item) => item.id === tenantId) ?? null);
        setPlants(plantRows);
        setUsers(userRows.filter((user) => user.role === "org_admin"));
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : "Failed to load organisation");
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    }

    if (tenantId) {
      void load();
    }

    return () => {
      isMounted = false;
    };
  }, [tenantId]);

  const tabs = useMemo(
    () => buildAdminOrgTabs({
      plants: plants.length,
      users: users.length,
      hardware: hardwareCount,
      notificationUsage: 0,
      includeNotificationUsage: isSuperAdmin,
    }),
    [hardwareCount, isSuperAdmin, plants.length, users.length],
  );

  useEffect(() => {
    if (!tabs.some((tab) => tab.key === activeTab)) {
      setActiveTab(tabs[0]?.key ?? "plants");
    }
  }, [activeTab, tabs]);

  async function handleToggleOrg(): Promise<void> {
    if (!org) {
      return;
    }
    setUpdatingOrg(true);
    setError(null);
    try {
      const updated = org.is_active ? await authApi.suspendTenant(tenantId) : await authApi.reactivateTenant(tenantId);
      setOrg(updated);
      setToast(updated.is_active ? "Organisation reactivated." : "Organisation suspended. Login, refresh, invites, and new resource creation are now blocked.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update organisation status");
    } finally {
      setUpdatingOrg(false);
    }
  }

  async function handleTogglePlant(plant: PlantProfile): Promise<void> {
    setUpdatingPlantId(plant.id);
    setError(null);
    try {
      const updated = plant.is_active
        ? await authApi.deactivatePlant(tenantId, plant.id)
        : await authApi.reactivatePlant(tenantId, plant.id);
      setPlants((current) => current.map((row) => (row.id === updated.id ? updated : row)));
      setToast(updated.is_active ? "Plant reactivated." : "Plant deactivated. New user assignments and device onboarding are now blocked.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update plant status");
    } finally {
      setUpdatingPlantId(null);
    }
  }

  async function handleDeactivateUser(userId: string): Promise<void> {
    const confirmed = window.confirm("Are you sure? This will immediately log out the user.");
    if (!confirmed) {
      return;
    }

    setDeactivatingUserId(userId);
    try {
      await authApi.deactivateUser(tenantId, userId);
      setUsers((current) =>
        current.map((user) =>
          user.id === userId ? { ...user, is_active: false } : user,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to deactivate user");
    } finally {
      setDeactivatingUserId(null);
    }
  }

  async function handleReactivateUser(userId: string): Promise<void> {
    setReactivatingUserId(userId);
    try {
      await authApi.reactivateUser(tenantId, userId);
      setUsers((current) =>
        current.map((user) =>
          user.id === userId
            ? { ...user, is_active: true, lifecycle_state: "active", can_reactivate: false, can_deactivate: true }
            : user,
        ),
      );
      setToast("User reactivated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reactivate user");
    } finally {
      setReactivatingUserId(null);
    }
  }

  async function handleResendInvite(user: UserProfile): Promise<void> {
    setResendingInviteUserId(user.id);
    try {
      await authApi.resendInvitation(tenantId, user.id);
      setUsers((current) =>
        current.map((row) =>
          row.id === user.id
            ? { ...row, lifecycle_state: "invited", invite_status: "pending", can_resend_invite: true }
            : row,
        ),
      );
      setToast(user.invite_status === "pending" ? "Invite resent." : "New invite issued.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resend invite");
    } finally {
      setResendingInviteUserId(null);
    }
  }

  return (
    <>
      <div className="space-y-5">
        <PageHeader
          title={org?.name ?? "Organisation"}
          subtitle={org ? `Slug: ${org.slug}` : "Loading organisation context"}
          actions={org ? (
            <Button
              variant={org.is_active ? "danger" : "outline"}
              onClick={() => void handleToggleOrg()}
              disabled={updatingOrg}
              isLoading={updatingOrg}
            >
              {org.is_active ? "Suspend Organisation" : "Reactivate Organisation"}
            </Button>
          ) : undefined}
        />

        {org ? (
          <div className={`rounded-2xl border px-4 py-3 text-sm ${org.is_active ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
            {org.is_active
              ? "Organisation is active."
              : "Organisation is suspended. Users cannot log in or refresh sessions, and new invites, users, plants, and other important create flows are blocked until reactivation."}
          </div>
        ) : null}

        {tenantId ? <OrgFeatureAccessEditor tenantId={tenantId} mode="org_grants" /> : null}

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

        <div className="surface-panel overflow-hidden">
          <div className="flex flex-wrap gap-2 border-b border-[var(--border-subtle)] px-4 py-3 sm:px-5">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  "inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition-colors",
                  activeTab === tab.key
                    ? "bg-[var(--tone-info-bg)] text-[var(--tone-info-text)]"
                    : "text-[var(--text-secondary)] hover:bg-[var(--surface-1)] hover:text-[var(--text-primary)]",
                )}
              >
                <span>{tab.label}</span>
                <span className="rounded-full bg-black/5 px-2 py-0.5 text-xs">{tab.count}</span>
              </button>
            ))}
          </div>
        </div>

        {activeTab === "plants" ? (
          <SectionCard
            title="Plants"
            subtitle="Physical factory sites, campuses, and production units inside this organisation."
            actions={(
              <Button onClick={() => setIsCreatePlantOpen(true)}>
                Add Plant
              </Button>
            )}
          >
            {isLoading ? (
              <DataSkeletonTable columns={["Name", "Location", "Timezone", "Status", "Created", "Actions"]} />
            ) : plants.length === 0 ? (
              <EmptyState message="No plants yet. Add the first plant to start assigning users and devices." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Location</TableHead>
                    <TableHead>Timezone</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {plants.map((plant) => (
                    <TableRow key={plant.id}>
                      <TableCell className="font-medium">{plant.name}</TableCell>
                      <TableCell>{plant.location || "—"}</TableCell>
                      <TableCell>{plant.timezone}</TableCell>
                      <TableCell>
                        <Badge variant={plant.is_active ? "success" : "error"}>
                          {plant.is_active ? "Active" : "Inactive"}
                        </Badge>
                      </TableCell>
                      <TableCell>{formatIST(plant.created_at, "Unknown")}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end">
                          <Button
                            size="sm"
                            variant={plant.is_active ? "danger" : "outline"}
                            disabled={updatingPlantId === plant.id}
                            isLoading={updatingPlantId === plant.id}
                            onClick={() => void handleTogglePlant(plant)}
                          >
                            {plant.is_active ? "Deactivate" : "Reactivate"}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </SectionCard>
        ) : activeTab === "users" ? (
          <SectionCard
            title="Org admins"
            subtitle="Users on this page can manage plants and invite plant-scoped operators inside the organisation."
            actions={(
              <Button onClick={() => setIsCreateOrgAdminOpen(true)}>
                Invite Org Admin
              </Button>
            )}
          >
            {isLoading ? (
              <DataSkeletonTable columns={["User", "Role", "Status", "Last login", "Actions"]} />
            ) : users.length === 0 ? (
              <EmptyState message="No org admins yet. Invite one to delegate organisation setup and user access." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>User</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last login</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((user) => {
                    const status = getLifecycleStatus(user);
                    const actions = getLifecycleActions(user);
                    return (
                    <TableRow key={user.id}>
                      <TableCell className="whitespace-normal">
                        <div>
                          <div className="font-medium">{user.full_name || "Unnamed user"}</div>
                          <div className="mt-0.5 text-xs text-[var(--text-secondary)]">{user.email}</div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <RoleBadge role="org_admin" size="sm" />
                      </TableCell>
                      <TableCell>
                        <Badge variant={status.variant}>{status.label}</Badge>
                      </TableCell>
                      <TableCell>
                        {user.last_login_at ? `${formatIST(user.last_login_at)} ${getRelativeTime(user.last_login_at)}`.trim() : "Never"}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          {actions.includes("resend_invite") || actions.includes("reinvite") ? (
                            <Button
                              variant="outline"
                              size="sm"
                              isLoading={resendingInviteUserId === user.id}
                              disabled={resendingInviteUserId === user.id}
                              onClick={() => void handleResendInvite(user)}
                            >
                              {actions.includes("resend_invite") ? "Resend invite" : "Reinvite"}
                            </Button>
                          ) : null}
                          {actions.includes("reactivate") ? (
                            <Button
                              variant="outline"
                              size="sm"
                              isLoading={reactivatingUserId === user.id}
                              disabled={reactivatingUserId === user.id}
                              onClick={() => void handleReactivateUser(user.id)}
                            >
                              Reactivate
                            </Button>
                          ) : null}
                          {actions.includes("deactivate") ? (
                            <Button
                              variant="danger"
                              size="sm"
                              disabled={deactivatingUserId === user.id}
                              isLoading={deactivatingUserId === user.id}
                              onClick={() => void handleDeactivateUser(user.id)}
                            >
                              Deactivate
                            </Button>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </SectionCard>
        ) : activeTab === "hardware" ? (
          <OrgHardwareTab
            orgId={tenantId}
            plants={plants}
            active={activeTab === "hardware"}
            onHardwareCountChange={setHardwareCount}
          />
        ) : (
          <OrgNotificationUsageTab
            orgId={tenantId}
            active={activeTab === "notification_usage"}
          />
        )}
      </div>

      <CreatePlantModal
        tenantId={tenantId}
        isOpen={isCreatePlantOpen}
        onClose={() => setIsCreatePlantOpen(false)}
        onSuccess={(newPlant) => {
          setPlants((current) => [newPlant, ...current]);
        }}
      />

      <CreateOrgAdminModal
        tenantId={tenantId}
        isOpen={isCreateOrgAdminOpen}
        onClose={() => setIsCreateOrgAdminOpen(false)}
        onSuccess={(newUser) => {
          setUsers((current) => {
            const existingIndex = current.findIndex((user) => user.id === newUser.id);
            if (existingIndex >= 0) {
              return current.map((user) => (user.id === newUser.id ? newUser : user));
            }
            return [newUser, ...current];
          });
          setToast("Org admin invite issued.");
        }}
      />
    </>
  );
}
