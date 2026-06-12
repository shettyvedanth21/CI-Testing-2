"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { authApi, type TenantProfile } from "@/lib/authApi";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface CreateOrgModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (tenant: TenantProfile) => void;
}

function autoSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .slice(0, 50);
}

function isValidSlug(slug: string): boolean {
  return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug);
}

export function CreateOrgModal({ isOpen, onClose, onSuccess }: CreateOrgModalProps) {
  const titleId = useId();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setName("");
      setSlug("");
      setSlugTouched(false);
      setError(null);
      setIsSubmitting(false);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!slugTouched) {
      setSlug(autoSlug(name));
    }
  }, [name, slugTouched]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    const trimmedName = name.trim();
    const trimmedSlug = slug.trim();

    if (trimmedName.length < 2) {
      setError("Organisation name must be at least 2 characters.");
      return;
    }

    if (!isValidSlug(trimmedSlug)) {
      setError("Slug must be lowercase letters, numbers, and hyphens only");
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const newTenant = await authApi.createTenant({ name: trimmedName, slug: trimmedSlug });
      onSuccess(newTenant);
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to create organisation";
      setError(
        message.toLowerCase().includes("taken")
          ? "This slug is already taken. Please choose another."
          : message,
      );
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
        aria-label="Close create organisation modal"
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
            New organisation
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Create a tenant workspace for a factory group, business unit, or customer site.
          </p>
        </div>

        <form className="space-y-4 p-5" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            label="Organisation name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Acme Factory"
            minLength={2}
            required
            disabled={isSubmitting}
          />

          <div className="space-y-1">
            <Input
              label="Slug"
              value={slug}
              onChange={(event) => {
                setSlugTouched(true);
                setSlug(event.target.value.toLowerCase());
              }}
              placeholder="acme-factory"
              required
              disabled={isSubmitting}
            />
            <p className="text-xs text-[var(--text-tertiary)]">
              Used in URLs, cannot be changed later.
            </p>
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
            <Button type="submit" isLoading={isSubmitting} disabled={isSubmitting}>
              Create organisation
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
