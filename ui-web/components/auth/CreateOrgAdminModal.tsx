"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { authApi, type UserProfile } from "@/lib/authApi";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface CreateOrgAdminModalProps {
  tenantId: string;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (user: UserProfile) => void;
}

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function CreateOrgAdminModal({
  tenantId,
  isOpen,
  onClose,
  onSuccess,
}: CreateOrgAdminModalProps) {
  const titleId = useId();
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setEmail("");
      setFullName("");
      setError(null);
      setIsSubmitting(false);
    }
  }, [isOpen]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    if (!isValidEmail(email.trim())) {
      setError("Enter a valid email address.");
      return;
    }
    if (fullName.trim().length < 2) {
      setError("Full name must be at least 2 characters.");
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const user = await authApi.createTenantAdmin({
        email: email.trim(),
        full_name: fullName.trim(),
        tenant_id: tenantId,
      });
      onSuccess(user);
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to create org admin";
      setError(
        message.toLowerCase().includes("reactivate")
          ? "This org admin exists but is deactivated. Use Reactivate from the org admin table."
          : message.toLowerCase().includes("registered") || message.toLowerCase().includes("taken")
          ? "This email is already registered."
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
        aria-label="Close invite org admin modal"
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
            Invite org admin
          </h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            This user will receive an email invite and set their own password securely.
          </p>
        </div>

        <form className="space-y-4 p-5" onSubmit={(event) => void handleSubmit(event)}>
          <Input
            label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="admin@factory.com"
            required
            disabled={isSubmitting}
          />
          <Input
            label="Full name"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            placeholder="Plant administrator"
            minLength={2}
            required
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
              Create org admin
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
