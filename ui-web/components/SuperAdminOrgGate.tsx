"use client";

import { useEffect, useMemo, useState } from "react";
import { authApi, type TenantProfile } from "@/lib/authApi";
import { useAuth } from "@/lib/authContext";
import { clearSelectedTenant, setSelectedTenantId, useTenantStore } from "@/lib/tenantStore";

type OrgDirectoryState = {
  tenants: TenantProfile[];
  loading: boolean;
  error: string | null;
};

function useOrgDirectory(enabled: boolean): OrgDirectoryState {
  const [state, setState] = useState<OrgDirectoryState>({
    tenants: [],
    loading: enabled,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      if (!enabled) {
        setState({ tenants: [], loading: false, error: null });
        return;
      }

      setState((prev) => ({ ...prev, loading: true, error: null }));
      try {
        const tenants = await authApi.listTenants();
        if (!cancelled) {
          setState({ tenants, loading: false, error: null });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            tenants: [],
            loading: false,
            error: error instanceof Error ? error.message : "Failed to load organisations",
          });
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return state;
}

function OrgSelectorPanel() {
  const { selectedTenantId } = useTenantStore();
  const [pendingTenantId, setPendingTenantId] = useState(selectedTenantId ?? "");
  const { tenants, loading, error } = useOrgDirectory(true);
  const { refetchMe } = useAuth();
  const selectedOrg = useMemo(
    () => tenants.find((tenant) => tenant.id === selectedTenantId) ?? null,
    [tenants, selectedTenantId],
  );

  useEffect(() => {
    if (selectedTenantId) {
      setPendingTenantId(selectedTenantId);
    } else if (tenants.length > 0 && pendingTenantId && !tenants.some((tenant) => tenant.id === pendingTenantId)) {
      setPendingTenantId(tenants[0].id);
    }
  }, [pendingTenantId, selectedTenantId, tenants]);

  return (
    <div className="mx-auto flex min-h-[60vh] w-full max-w-2xl items-center px-4 py-10 sm:px-6 lg:px-8">
      <div className="surface-panel w-full border border-[var(--border-subtle)] p-6 shadow-[var(--shadow-raised)]">
        <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-[var(--text-tertiary)]">
          Tenant Scope Required
        </div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
          Select an organisation to continue
        </h1>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          Super-admin requests need an explicit organisation context before any tenant-scoped data can load.
        </p>

        <div className="mt-6 space-y-3">
          {loading ? (
            <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-3 text-sm text-[var(--text-secondary)]">
              Loading organisations...
            </div>
          ) : error ? (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          ) : tenants.length === 0 ? (
            <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              No organisations exist yet. Create one from Admin before opening tenant pages.
            </div>
          ) : (
            <label className="block space-y-2">
              <span className="text-sm font-medium text-[var(--text-primary)]">Organisation</span>
              <select
                className="w-full rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-4 py-3 text-sm text-[var(--text-primary)] outline-none ring-0"
                value={pendingTenantId}
                onChange={(event) => setPendingTenantId(event.target.value)}
              >
                <option value="">Choose an organisation</option>
                {tenants.map((tenant) => (
                  <option key={tenant.id} value={tenant.id}>
                    {tenant.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <button
            type="button"
            className="inline-flex items-center justify-center rounded-2xl bg-[var(--tone-info-solid)] px-4 py-2.5 text-sm font-medium text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!pendingTenantId || loading || Boolean(error)}
          onClick={() => {
            setSelectedTenantId(pendingTenantId);
            void refetchMe();
          }}
        >
          Continue
        </button>
        </div>

        {selectedOrg ? (
          <p className="mt-4 text-xs text-[var(--text-tertiary)]">
            Current selection: <span className="font-medium text-[var(--text-secondary)]">{selectedOrg.name}</span>
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function SuperAdminOrgGate({ children }: { children: React.ReactNode }) {
  const { me, isLoading } = useAuth();
  const { selectedTenantId } = useTenantStore();

  if (isLoading || !me) {
    return null;
  }

  if (me.user.role !== "super_admin") {
    return <>{children}</>;
  }

  if (selectedTenantId) {
    return <>{children}</>;
  }

  return <OrgSelectorPanel />;
}

export function SuperAdminOrgSwitcher() {
  const { me, refetchMe } = useAuth();
  const { selectedTenantId } = useTenantStore();
  const { tenants, loading } = useOrgDirectory(Boolean(me && me.user.role === "super_admin" && selectedTenantId));

  if (!me || me.user.role !== "super_admin" || !selectedTenantId) {
    return null;
  }

  const selectedOrg = tenants.find((tenant) => tenant.id === selectedTenantId) ?? null;

  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-3">
      <div className="text-[11px] uppercase tracking-[0.16em] text-[var(--text-tertiary)]">
        Active organisation
      </div>
      <div className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
        {loading ? "Loading..." : selectedOrg?.name ?? "Selected organisation"}
      </div>
      <button
        type="button"
        className="mt-3 inline-flex items-center justify-center rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 py-2 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
        onClick={() => {
          clearSelectedTenant();
          void refetchMe();
        }}
      >
        Switch org
      </button>
    </div>
  );
}
