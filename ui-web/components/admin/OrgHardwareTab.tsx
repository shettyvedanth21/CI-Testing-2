"use client";

import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import type { PlantProfile } from "@/lib/authApi";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { SectionCard } from "@/components/ui/page-scaffold";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatIST } from "@/lib/utils";
import { getDevices, type Device } from "@/lib/deviceApi";
import {
  createHardwareUnit,
  decommissionHardwareInstallation,
  installHardwareOnDevice,
  listHardwareMappings,
  listHardwareInstallationHistory,
  listHardwareUnits,
  updateHardwareUnit,
  type DeviceHardwareMapping,
  type DeviceHardwareInstallation,
  type HardwareUnit,
  type HardwareUnitCreateInput,
  type HardwareUnitUpdateInput,
} from "@/lib/hardwareApi";
import {
  buildHardwareUnitUpdatePayload,
  buildInstallableDeviceOptions,
  buildInventoryRows,
  filterInstallationHistory,
  flattenDeviceHistory,
  getHardwareUnitStatusLabel,
  getHardwareUnitTypeLabel,
  getInstallationRoleLabel,
  getPlantName,
  HARDWARE_UNIT_TYPE_OPTIONS,
  INSTALLATION_ROLE_OPTIONS,
  isAllowedHardwareUnitType,
} from "@/lib/hardwareAdmin";

type OrgHardwareTabProps = {
  orgId: string;
  plants: PlantProfile[];
  active: boolean;
  onHardwareCountChange: (count: number) => void;
};

type HardwareFormValues = {
  plant_id: string;
  unit_type: string;
  unit_name: string;
  manufacturer: string;
  model: string;
  serial_number: string;
  status: "available" | "retired";
};

type InstallFormValues = {
  hardwareUnitId: string;
  plantId: string;
  deviceId: string;
  installationRole: string;
  commissionedAt: string;
  notes: string;
};

type DecommissionFormValues = {
  decommissionedAt: string;
  notes: string;
};

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
        {Array.from({ length: 4 }).map((_, index) => (
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

function toDatetimeLocal(value: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function HardwareUnitModal({
  isOpen,
  plants,
  hardwareUnit,
  onClose,
  onSubmit,
}: {
  isOpen: boolean;
  plants: PlantProfile[];
  hardwareUnit: HardwareUnit | null;
  onClose: () => void;
  onSubmit: (payload: HardwareUnitCreateInput | HardwareUnitUpdateInput) => Promise<void>;
}) {
  const titleId = useId();
  const [form, setForm] = useState<HardwareFormValues>({
    plant_id: plants[0]?.id ?? "",
    unit_type: "",
    unit_name: "",
    manufacturer: "",
    model: "",
    serial_number: "",
    status: "available",
  });
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const unitTypeOptions = useMemo(() => {
    if (!hardwareUnit || !form.unit_type || isAllowedHardwareUnitType(form.unit_type)) {
      return HARDWARE_UNIT_TYPE_OPTIONS;
    }
    return [
      { value: form.unit_type, label: `Legacy value: ${form.unit_type}` },
      ...HARDWARE_UNIT_TYPE_OPTIONS,
    ];
  }, [form.unit_type, hardwareUnit]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setForm({
      plant_id: hardwareUnit?.plant_id ?? plants[0]?.id ?? "",
      unit_type: hardwareUnit?.unit_type ?? "",
      unit_name: hardwareUnit?.unit_name ?? "",
      manufacturer: hardwareUnit?.manufacturer ?? "",
      model: hardwareUnit?.model ?? "",
      serial_number: hardwareUnit?.serial_number ?? "",
      status: hardwareUnit?.status ?? "available",
    });
    setError(null);
    setIsSubmitting(false);
  }, [hardwareUnit, isOpen, plants]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!form.unit_type.trim()) {
      setError("Unit type is required.");
      return;
    }
    if (!form.unit_name.trim()) {
      setError("Unit name is required.");
      return;
    }
    if (!form.plant_id) {
      setError("Plant selection is required.");
      return;
    }

    setError(null);
    setIsSubmitting(true);
    try {
      await onSubmit({
        plant_id: form.plant_id,
        unit_type: form.unit_type.trim(),
        unit_name: form.unit_name.trim(),
        manufacturer: form.manufacturer.trim() || undefined,
        model: form.model.trim() || undefined,
        serial_number: form.serial_number.trim() || undefined,
        status: form.status,
      });
      onClose();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Failed to save hardware unit");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (!isOpen) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button type="button" aria-label="Close hardware unit modal" className="absolute inset-0" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 w-full max-w-2xl rounded-[1.5rem] border border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-[var(--shadow-raised)]"
      >
        <div className="border-b border-[var(--border-subtle)] px-5 py-4">
          <h2 id={titleId} className="text-lg font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            {hardwareUnit ? "Edit hardware unit" : "Create hardware unit"}
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Track the hardware category, this unit's label, and the plant that owns the inventory record.
          </p>
        </div>

        <form className="space-y-4 p-5" onSubmit={(event) => void handleSubmit(event)}>
          {hardwareUnit ? (
            <Input
              label="Hardware unit ID"
              value={hardwareUnit.hardware_unit_id}
              disabled
            />
          ) : null}
          <div className="grid gap-4 md:grid-cols-2">
            <Select
              label="Plant"
              value={form.plant_id}
              onChange={(event) => setForm((current) => ({ ...current, plant_id: event.target.value }))}
              disabled={isSubmitting}
              options={[
                { value: "", label: "Select plant" },
                ...plants.map((plant) => ({ value: plant.id, label: plant.name })),
              ]}
            />
            <Select
              label="Unit type"
              value={form.unit_type}
              onChange={(event) => setForm((current) => ({ ...current, unit_type: event.target.value }))}
              disabled={isSubmitting}
              options={[
                { value: "", label: "Select hardware category" },
                ...unitTypeOptions,
              ]}
              helperText={
                hardwareUnit && form.unit_type && !isAllowedHardwareUnitType(form.unit_type)
                  ? "This unit uses a legacy saved category. You can keep it while updating other fields, or choose a supported category now."
                  : "Unit type is the hardware category for this inventory record."
              }
            />
            <Input
              label="Unit name"
              value={form.unit_name}
              onChange={(event) => setForm((current) => ({ ...current, unit_name: event.target.value }))}
              disabled={isSubmitting}
              required
              helperText="Use a specific label for this physical unit, such as CT1 or Main Energy Meter."
            />
            <Select
              label="Status"
              value={form.status}
              onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as HardwareFormValues["status"] }))}
              disabled={isSubmitting}
              options={[
                { value: "available", label: "In Inventory" },
                { value: "retired", label: "Retired" },
              ]}
            />
            <Input
              label="Manufacturer"
              value={form.manufacturer}
              onChange={(event) => setForm((current) => ({ ...current, manufacturer: event.target.value }))}
              disabled={isSubmitting}
            />
            <Input
              label="Model"
              value={form.model}
              onChange={(event) => setForm((current) => ({ ...current, model: event.target.value }))}
              disabled={isSubmitting}
            />
            <Input
              label="Serial number"
              value={form.serial_number}
              onChange={(event) => setForm((current) => ({ ...current, serial_number: event.target.value }))}
              disabled={isSubmitting}
            />
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
            <Button type="submit" isLoading={isSubmitting}>
              {hardwareUnit ? "Save changes" : "Create hardware"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function InstallHardwareModal({
  isOpen,
  hardwareUnit,
  devices,
  plants,
  onClose,
  onSubmit,
}: {
  isOpen: boolean;
  hardwareUnit: HardwareUnit | null;
  devices: Device[];
  plants: PlantProfile[];
  onClose: () => void;
  onSubmit: (deviceId: string, payload: { hardware_unit_id: string; installation_role: string; commissioned_at?: string | null; notes?: string }) => Promise<void>;
}) {
  const titleId = useId();
  const [form, setForm] = useState<InstallFormValues>({
    hardwareUnitId: hardwareUnit?.hardware_unit_id ?? "",
    plantId: hardwareUnit?.plant_id ?? "",
    deviceId: "",
    installationRole: "",
    commissionedAt: "",
    notes: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const plantName = hardwareUnit ? getPlantName(plants, hardwareUnit.plant_id) : "";
  const unitTypeLabel = hardwareUnit ? getHardwareUnitTypeLabel(hardwareUnit.unit_type) : "";

  const deviceOptions = useMemo(
    () => [{ value: "", label: "Select device" }, ...buildInstallableDeviceOptions(devices, form.plantId)],
    [devices, form.plantId],
  );

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setForm({
      hardwareUnitId: hardwareUnit?.hardware_unit_id ?? "",
      plantId: hardwareUnit?.plant_id ?? "",
      deviceId: "",
      installationRole: "",
      commissionedAt: "",
      notes: "",
    });
    setError(null);
    setIsSubmitting(false);
  }, [hardwareUnit, isOpen]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!form.deviceId) {
      setError("Select a device for this installation.");
      return;
    }
    if (!form.installationRole.trim()) {
      setError("Installation role is required.");
      return;
    }

    setError(null);
    setIsSubmitting(true);
    try {
      await onSubmit(form.deviceId, {
        hardware_unit_id: form.hardwareUnitId,
        installation_role: form.installationRole.trim(),
        commissioned_at: form.commissionedAt ? new Date(form.commissionedAt).toISOString() : null,
        notes: form.notes.trim() || undefined,
      });
      onClose();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Failed to install hardware");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (!isOpen || !hardwareUnit) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button type="button" aria-label="Close install hardware modal" className="absolute inset-0" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 w-full max-w-xl rounded-[1.5rem] border border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-[var(--shadow-raised)]"
      >
        <div className="border-b border-[var(--border-subtle)] px-5 py-4">
          <h2 id={titleId} className="text-lg font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            Install hardware on device
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Assign this hardware unit to a device role. Only devices from {plantName} are available for selection.
          </p>
        </div>
        <form className="space-y-4 p-5" onSubmit={(event) => void handleSubmit(event)}>
          <Input label="Hardware unit ID" value={hardwareUnit.hardware_unit_id} disabled />
          <Input label="Plant" value={plantName} disabled />
          <Input label="Unit type" value={unitTypeLabel} disabled />
          <Input label="Unit name" value={hardwareUnit.unit_name} disabled />
          <Select
            label="Device"
            value={form.deviceId}
            onChange={(event) => setForm((current) => ({ ...current, deviceId: event.target.value }))}
            options={deviceOptions}
            disabled={isSubmitting}
          />
          <Select
            label="Installation role"
            value={form.installationRole}
            onChange={(event) => setForm((current) => ({ ...current, installationRole: event.target.value }))}
            disabled={isSubmitting}
            options={[
              { value: "", label: "Select device role" },
              ...INSTALLATION_ROLE_OPTIONS,
            ]}
          />
          <Input
            label="Commissioned at"
            type="datetime-local"
            value={form.commissionedAt}
            onChange={(event) => setForm((current) => ({ ...current, commissionedAt: event.target.value }))}
            disabled={isSubmitting}
            helperText="Commissioning date and time for this installation event."
          />
          <div className="space-y-1">
            <label className="block text-sm font-medium text-slate-700" htmlFor="install-notes">
              Notes
            </label>
            <textarea
              id="install-notes"
              value={form.notes}
              onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))}
              className="min-h-24 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 py-2 text-sm text-[var(--text-primary)] shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
              disabled={isSubmitting}
            />
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
            <Button type="submit" isLoading={isSubmitting}>
              Install hardware
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function DecommissionInstallationModal({
  installation,
  isOpen,
  onClose,
  onSubmit,
}: {
  installation: DeviceHardwareInstallation | null;
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (installationId: number, payload: { decommissioned_at?: string | null; notes?: string }) => Promise<void>;
}) {
  const titleId = useId();
  const [form, setForm] = useState<DecommissionFormValues>({
    decommissionedAt: "",
    notes: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setForm({
      decommissionedAt: installation?.decommissioned_at ? toDatetimeLocal(installation.decommissioned_at) : "",
      notes: installation?.notes ?? "",
    });
    setError(null);
    setIsSubmitting(false);
  }, [installation, isOpen]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!installation) {
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      await onSubmit(installation.id, {
        decommissioned_at: form.decommissionedAt ? new Date(form.decommissionedAt).toISOString() : null,
        notes: form.notes.trim() || undefined,
      });
      onClose();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Failed to decommission installation");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (!isOpen || !installation) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-4">
      <button type="button" aria-label="Close decommission modal" className="absolute inset-0" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 w-full max-w-xl rounded-[1.5rem] border border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-[var(--shadow-raised)]"
      >
        <div className="border-b border-[var(--border-subtle)] px-5 py-4">
          <h2 id={titleId} className="text-lg font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            Decommission installation
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Remove {installation.hardware_unit_id} from {installation.device_id} and preserve the audit trail.
          </p>
        </div>
        <form className="space-y-4 p-5" onSubmit={(event) => void handleSubmit(event)}>
          <Input label="Hardware unit" value={installation.hardware_unit_id} disabled />
          <Input label="Device" value={installation.device_id} disabled />
          <Input
            label="Decommissioned at"
            type="datetime-local"
            value={form.decommissionedAt}
            onChange={(event) => setForm((current) => ({ ...current, decommissionedAt: event.target.value }))}
            disabled={isSubmitting}
          />
          <div className="space-y-1">
            <label className="block text-sm font-medium text-slate-700" htmlFor="decommission-notes">
              Notes
            </label>
            <textarea
              id="decommission-notes"
              value={form.notes}
              onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))}
              className="min-h-24 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 py-2 text-sm text-[var(--text-primary)] shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
              disabled={isSubmitting}
            />
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
            <Button type="submit" variant="danger" isLoading={isSubmitting}>
              Decommission
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function OrgHardwareTab({ orgId, plants, active, onHardwareCountChange }: OrgHardwareTabProps) {
  const [hardwareUnits, setHardwareUnits] = useState<HardwareUnit[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [hardwareMappings, setHardwareMappings] = useState<DeviceHardwareMapping[]>([]);
  const [installationHistoryByDeviceId, setInstallationHistoryByDeviceId] = useState<Record<string, DeviceHardwareInstallation[]>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [plantFilter, setPlantFilter] = useState<string>("all");
  const [historyDeviceFilter, setHistoryDeviceFilter] = useState<string>("all");
  const [historyHardwareFilter, setHistoryHardwareFilter] = useState<string>("all");
  const [historyStateFilter, setHistoryStateFilter] = useState<"all" | "active" | "decommissioned">("all");
  const [editingHardwareUnit, setEditingHardwareUnit] = useState<HardwareUnit | null>(null);
  const [installTarget, setInstallTarget] = useState<HardwareUnit | null>(null);
  const [decommissionTarget, setDecommissionTarget] = useState<DeviceHardwareInstallation | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);

  useEffect(() => {
    setHardwareUnits([]);
    setDevices([]);
    setHardwareMappings([]);
    setInstallationHistoryByDeviceId({});
    setError(null);
    setToast(null);
    setPlantFilter("all");
    setHistoryDeviceFilter("all");
    setHistoryHardwareFilter("all");
    setHistoryStateFilter("all");
    setEditingHardwareUnit(null);
    setInstallTarget(null);
    setDecommissionTarget(null);
    setShowCreateModal(false);
    setHasLoadedOnce(false);
    onHardwareCountChange(0);
  }, [onHardwareCountChange, orgId]);

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timeout = window.setTimeout(() => setToast(null), 3000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  async function loadHardwareContext(): Promise<void> {
    setIsLoading(true);
    setError(null);
    try {
      const [nextHardwareUnits, nextDevices, nextInstallations, nextMappings] = await Promise.all([
        listHardwareUnits(),
        getDevices(),
        listHardwareInstallationHistory(),
        listHardwareMappings(),
      ]);
      setHardwareUnits(nextHardwareUnits);
      onHardwareCountChange(nextHardwareUnits.length);
      setDevices(nextDevices);
      setHardwareMappings(nextMappings);
      setInstallationHistoryByDeviceId(
        nextInstallations.reduce<Record<string, DeviceHardwareInstallation[]>>((grouped, installation) => {
          const current = grouped[installation.device_id] ?? [];
          current.push(installation);
          grouped[installation.device_id] = current;
          return grouped;
        }, {}),
      );
      setHasLoadedOnce(true);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load hardware data");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    if (!active || !orgId || hasLoadedOnce) {
      return;
    }
    void loadHardwareContext();
  }, [active, hasLoadedOnce, orgId]);

  const installations = useMemo(
    () => flattenDeviceHistory(installationHistoryByDeviceId),
    [installationHistoryByDeviceId],
  );

  const inventoryRows = useMemo(
    () =>
      buildInventoryRows({
        hardwareUnits,
        devices,
        plants,
        installations,
        plantFilter: plantFilter === "all" ? null : plantFilter,
      }),
    [devices, hardwareUnits, installations, plantFilter, plants],
  );

  const filteredHistory = useMemo(
    () =>
      filterInstallationHistory(installations, {
        plantId: plantFilter === "all" ? null : plantFilter,
        deviceId: historyDeviceFilter === "all" ? null : historyDeviceFilter,
        hardwareUnitId: historyHardwareFilter === "all" ? null : historyHardwareFilter,
        state: historyStateFilter,
      }),
    [historyDeviceFilter, historyHardwareFilter, historyStateFilter, installations, plantFilter],
  );

  async function handleCreateHardware(payload: HardwareUnitCreateInput | HardwareUnitUpdateInput): Promise<void> {
    if (!editingHardwareUnit) {
      const created = await createHardwareUnit(payload as HardwareUnitCreateInput);
      setToast(`Hardware unit ${created.hardware_unit_id} created.`);
    }
    await loadHardwareContext();
  }

  async function handleUpdateHardware(payload: HardwareUnitCreateInput | HardwareUnitUpdateInput): Promise<void> {
    if (!editingHardwareUnit) {
      return;
    }
    const updatePayload = buildHardwareUnitUpdatePayload(editingHardwareUnit, {
      plant_id: payload.plant_id ?? editingHardwareUnit.plant_id,
      unit_type: payload.unit_type ?? editingHardwareUnit.unit_type,
      unit_name: payload.unit_name ?? editingHardwareUnit.unit_name,
      manufacturer: payload.manufacturer,
      model: payload.model,
      serial_number: payload.serial_number,
      status: payload.status ?? editingHardwareUnit.status,
    });
    await updateHardwareUnit(editingHardwareUnit.hardware_unit_id, updatePayload);
    setToast("Hardware unit updated.");
    await loadHardwareContext();
  }

  async function handleInstallHardware(
    deviceId: string,
    payload: { hardware_unit_id: string; installation_role: string; commissioned_at?: string | null; notes?: string },
  ): Promise<void> {
    await installHardwareOnDevice(deviceId, payload);
    setToast("Hardware installed on device.");
    await loadHardwareContext();
  }

  async function handleDecommissionInstallation(
    installationId: number,
    payload: { decommissioned_at?: string | null; notes?: string },
  ): Promise<void> {
    await decommissionHardwareInstallation(installationId, payload);
    setToast("Installation decommissioned.");
    await loadHardwareContext();
  }

  return (
    <>
      <div className="space-y-5">
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

        <SectionCard
          title="Hardware inventory"
          subtitle="Manage physical hardware units for this organisation with plant context, active assignment, and lifecycle status."
          actions={(
            <div className="flex flex-wrap gap-2">
              <Select
                aria-label="Filter hardware by plant"
                className="min-w-48"
                value={plantFilter}
                onChange={(event) => setPlantFilter(event.target.value)}
                options={[
                  { value: "all", label: "All plants" },
                  ...plants.map((plant) => ({ value: plant.id, label: plant.name })),
                ]}
              />
              <Button onClick={() => setShowCreateModal(true)}>Add hardware</Button>
            </div>
          )}
        >
          {isLoading && !hasLoadedOnce ? (
            <DataSkeletonTable columns={["Hardware unit", "Plant", "Type", "Status", "Current assignment", "Actions"]} />
          ) : inventoryRows.length === 0 ? (
            <EmptyState message="No hardware units found for this organisation and plant filter." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Hardware unit</TableHead>
                  <TableHead>Plant</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Current assignment</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {inventoryRows.map((row) => (
                  <TableRow key={row.hardwareUnit.hardware_unit_id}>
                    <TableCell className="whitespace-normal">
                      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--text-tertiary)]">
                        {row.hardwareUnit.hardware_unit_id}
                      </div>
                      <div className="font-medium">{row.hardwareUnit.unit_name}</div>
                      <div className="mt-0.5 text-sm text-[var(--text-secondary)]">
                        {row.unitTypeLabel}
                      </div>
                      <div className="mt-0.5 text-xs text-[var(--text-secondary)]">
                        Serial: {row.hardwareUnit.serial_number || "—"}
                      </div>
                      <div className="mt-0.5 text-xs text-[var(--text-tertiary)]">
                        {row.hardwareUnit.manufacturer || "Unknown manufacturer"} / {row.hardwareUnit.model || "Unknown model"}
                      </div>
                    </TableCell>
                    <TableCell>{row.plantName}</TableCell>
                    <TableCell>{row.unitTypeLabel}</TableCell>
                    <TableCell>
                      <Badge variant={row.currentInstallation ? "success" : row.hardwareUnit.status === "retired" ? "error" : "default"}>
                        {row.statusLabel}
                      </Badge>
                    </TableCell>
                    <TableCell className="whitespace-normal">
                      {row.currentInstallation && row.currentDevice ? (
                        <div>
                          <div className="font-medium">{row.currentDevice.id}</div>
                          <div className="mt-0.5 text-xs text-[var(--text-secondary)]">
                            {row.currentDevice.name} · role {row.currentInstallationRoleLabel}
                          </div>
                          <div className="mt-0.5 text-xs text-[var(--text-tertiary)]">
                            Commissioned {formatIST(row.currentInstallation.commissioned_at, "Unknown")}
                          </div>
                        </div>
                      ) : (
                        <span className="text-sm text-[var(--text-secondary)]">Not currently assigned</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setEditingHardwareUnit(row.hardwareUnit);
                            setShowCreateModal(true);
                          }}
                        >
                          Edit
                        </Button>
                        {row.currentInstallation ? (
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => setDecommissionTarget(row.currentInstallation)}
                          >
                            Decommission
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            onClick={() => setInstallTarget(row.hardwareUnit)}
                            disabled={row.hardwareUnit.status === "retired"}
                          >
                            Install
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </SectionCard>

        <SectionCard
          title="Current device mappings"
          subtitle="Readable view of active hardware assigned to devices, with plant, slot, and unit details for audit checks."
        >
          {isLoading && !hasLoadedOnce ? (
            <DataSkeletonTable columns={["Device ID", "Plant", "Slot / Role", "Hardware Unit ID", "Hardware Type", "Hardware Name", "Manufacturer", "Model", "Serial", "Status"]} />
          ) : hardwareMappings.filter((mapping) => plantFilter === "all" || mapping.plant_id === plantFilter).length === 0 ? (
            <EmptyState message="No active device hardware mappings match the current plant filter." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Device ID</TableHead>
                  <TableHead>Plant</TableHead>
                  <TableHead>Slot / Role</TableHead>
                  <TableHead>Hardware Unit ID</TableHead>
                  <TableHead>Hardware Type</TableHead>
                  <TableHead>Hardware Name</TableHead>
                  <TableHead>Manufacturer</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Serial</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {hardwareMappings
                  .filter((mapping) => plantFilter === "all" || mapping.plant_id === plantFilter)
                  .map((mapping) => (
                    <TableRow key={`${mapping.device_id}-${mapping.installation_role}-${mapping.hardware_unit_id}`}>
                      <TableCell>{mapping.device_id}</TableCell>
                      <TableCell>{mapping.plant_name}</TableCell>
                      <TableCell>{mapping.installation_role_label}</TableCell>
                      <TableCell>{mapping.hardware_unit_id}</TableCell>
                      <TableCell>{mapping.hardware_type_label}</TableCell>
                      <TableCell>{mapping.hardware_name}</TableCell>
                      <TableCell>{mapping.manufacturer || "—"}</TableCell>
                      <TableCell>{mapping.model || "—"}</TableCell>
                      <TableCell>{mapping.serial_number || "—"}</TableCell>
                      <TableCell>
                        <Badge variant={mapping.is_active ? "success" : "default"}>{mapping.status}</Badge>
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          )}
        </SectionCard>

        <SectionCard
          title="Installation history"
          subtitle="Audit hardware assignment changes across plants, devices, and roles. Active rows remain highlighted until decommissioned."
          actions={(
            <div className="grid gap-2 sm:grid-cols-4">
              <Select
                aria-label="Filter history by plant"
                value={plantFilter}
                onChange={(event) => setPlantFilter(event.target.value)}
                options={[
                  { value: "all", label: "All plants" },
                  ...plants.map((plant) => ({ value: plant.id, label: plant.name })),
                ]}
              />
              <Select
                aria-label="Filter history by device"
                value={historyDeviceFilter}
                onChange={(event) => setHistoryDeviceFilter(event.target.value)}
                options={[
                  { value: "all", label: "All devices" },
                  ...devices
                    .filter((device) => plantFilter === "all" || device.plant_id === plantFilter)
                    .map((device) => ({ value: device.id, label: `${device.id} · ${device.name}` })),
                ]}
              />
              <Select
                aria-label="Filter history by hardware unit"
                value={historyHardwareFilter}
                onChange={(event) => setHistoryHardwareFilter(event.target.value)}
                options={[
                  { value: "all", label: "All hardware" },
                  ...hardwareUnits
                    .filter((hardwareUnit) => plantFilter === "all" || hardwareUnit.plant_id === plantFilter)
                    .map((hardwareUnit) => ({ value: hardwareUnit.hardware_unit_id, label: hardwareUnit.hardware_unit_id })),
                ]}
              />
              <Select
                aria-label="Filter history by state"
                value={historyStateFilter}
                onChange={(event) => setHistoryStateFilter(event.target.value as "all" | "active" | "decommissioned")}
                options={[
                  { value: "all", label: "All states" },
                  { value: "active", label: "Active only" },
                  { value: "decommissioned", label: "Decommissioned only" },
                ]}
              />
            </div>
          )}
        >
          {isLoading && !hasLoadedOnce ? (
            <DataSkeletonTable columns={["Hardware", "Device", "Role", "Commissioned", "Decommissioned", "State"]} />
          ) : filteredHistory.length === 0 ? (
            <EmptyState message="No installation events match the selected filters." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Hardware</TableHead>
                  <TableHead>Device</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Commissioned</TableHead>
                  <TableHead>Decommissioned</TableHead>
                  <TableHead>State</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredHistory.map((installation) => (
                  <TableRow key={installation.id}>
                    <TableCell className="whitespace-normal">
                      <div className="font-medium">{installation.hardware_unit_id}</div>
                      <div className="mt-0.5 text-xs text-[var(--text-secondary)]">
                        {getPlantName(plants, installation.plant_id)}
                      </div>
                      {installation.notes ? (
                        <div className="mt-0.5 text-xs text-[var(--text-tertiary)]">{installation.notes}</div>
                      ) : null}
                    </TableCell>
                    <TableCell className="whitespace-normal">
                      <div className="font-medium">{installation.device_id}</div>
                      <div className="mt-0.5 text-xs text-[var(--text-secondary)]">
                        {devices.find((device) => device.id === installation.device_id)?.name || "Unknown device"}
                      </div>
                    </TableCell>
                    <TableCell>{getInstallationRoleLabel(installation.installation_role)}</TableCell>
                    <TableCell>{formatIST(installation.commissioned_at, "Unknown")}</TableCell>
                    <TableCell>{installation.decommissioned_at ? formatIST(installation.decommissioned_at, "Unknown") : "—"}</TableCell>
                    <TableCell>
                      <Badge variant={installation.is_active ? "success" : "default"}>
                        {installation.is_active ? "Active" : "Decommissioned"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </SectionCard>
      </div>

      <HardwareUnitModal
        isOpen={showCreateModal}
        plants={plants}
        hardwareUnit={editingHardwareUnit}
        onClose={() => {
          setShowCreateModal(false);
          setEditingHardwareUnit(null);
        }}
        onSubmit={(payload) => (editingHardwareUnit ? handleUpdateHardware(payload) : handleCreateHardware(payload))}
      />

      <InstallHardwareModal
        isOpen={Boolean(installTarget)}
        hardwareUnit={installTarget}
        devices={devices}
        plants={plants}
        onClose={() => setInstallTarget(null)}
        onSubmit={handleInstallHardware}
      />

      <DecommissionInstallationModal
        isOpen={Boolean(decommissionTarget)}
        installation={decommissionTarget}
        onClose={() => setDecommissionTarget(null)}
        onSubmit={handleDecommissionInstallation}
      />
    </>
  );
}
