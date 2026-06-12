"use client";

import type { MeResponse } from "./authApi.ts";

export function resolveScopedTenantId(me: MeResponse | null, selectedTenantId: string | null): string | null {
  if (!me) {
    return null;
  }

  if (me.user.role === "super_admin") {
    return selectedTenantId;
  }

  return me.user.tenant_id ?? me.tenant?.id ?? null;
}

export function resolveVisiblePlants<T extends { id: string }>(
  me: MeResponse | null,
  plants: T[],
): T[] {
  if (!me) {
    return [];
  }

  if (me.user.role === "super_admin" || me.user.role === "org_admin") {
    return plants;
  }

  const permittedPlantIds = new Set(me.plant_ids ?? []);
  return plants.filter((plant) => permittedPlantIds.has(plant.id));
}
