import { cn } from "@/lib/utils";
import { getStatusTone } from "@/lib/presentation";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="page-header flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h2 className="page-title">{title}</h2>
        {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:justify-end">{actions}</div> : null}
    </div>
  );
}

export function StatCard({
  label,
  value,
  meta,
  tone = "neutral",
  className,
}: {
  label: string;
  value: string;
  meta?: string;
  tone?: "success" | "warning" | "danger" | "info" | "neutral";
  className?: string;
}) {
  return (
    <div className={cn("surface-panel px-4 py-3", className)}>
      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">{label}</p>
      <div className="mt-2 flex items-center gap-2">
        <p className="text-2xl font-semibold tracking-[-0.02em] text-[var(--text-primary)]">{value}</p>
        <span className="status-pill" data-tone={tone} />
      </div>
      {meta ? <p className="mt-1 text-sm text-[var(--text-secondary)]">{meta}</p> : null}
    </div>
  );
}

export function SectionCard({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("surface-panel overflow-hidden", className)}>
      <div className="flex flex-col gap-3 border-b border-[var(--border-subtle)] px-4 py-3 sm:flex-row sm:items-start sm:justify-between sm:px-5">
        <div className="min-w-0">
          <h3 className="text-base font-semibold tracking-[-0.01em] text-[var(--text-primary)]">{title}</h3>
          {subtitle ? <p className="mt-0.5 text-sm text-[var(--text-secondary)]">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:justify-end">{actions}</div> : null}
      </div>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

export function FilterBar({ children }: { children: React.ReactNode }) {
  return (
    <div className="surface-panel mb-4 flex flex-col gap-2 px-3 py-2 sm:flex-row sm:flex-wrap sm:items-center sm:px-4">{children}</div>
  );
}

export function StatusPill({ status, className }: { status: string; className?: string }) {
  const tone = getStatusTone(status);
  return (
    <span className={cn("status-pill capitalize", className)} data-tone={tone}>
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
      {status}
    </span>
  );
}
