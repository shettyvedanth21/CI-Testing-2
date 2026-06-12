import React from 'react';
import { cn } from '@/lib/utils';

type BadgeVariant = 'default' | 'success' | 'warning' | 'error' | 'info' | 'critical';

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
  variant?: BadgeVariant;
  className?: string;
}

const variantStyles: Record<BadgeVariant, string> = {
  default: "bg-[var(--surface-2)] text-[var(--text-secondary)] border-[var(--border-subtle)]",
  success: "bg-[var(--tone-success-bg)] text-[var(--tone-success-text)] border-[var(--tone-success-border)]",
  warning: "bg-[var(--tone-warning-bg)] text-[var(--tone-warning-text)] border-[var(--tone-warning-border)]",
  error: "bg-[var(--tone-danger-bg)] text-[var(--tone-danger-text)] border-[var(--tone-danger-border)]",
  info: "bg-[var(--tone-info-bg)] text-[var(--tone-info-text)] border-[var(--tone-info-border)]",
  critical: "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-200",
};

export function Badge({
  children,
  variant = 'default',
  className,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        variantStyles[variant],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const normalizedStatus = status?.toLowerCase() || 'unknown';
  
  let variant: BadgeVariant = 'default';
  
  if (['active', 'online', 'running', 'healthy', 'up'].includes(normalizedStatus)) {
    variant = 'success';
  } else if (['inactive', 'offline', 'stopped', 'down', 'failed'].includes(normalizedStatus)) {
    variant = 'error';
  } else if (['overconsumption'].includes(normalizedStatus)) {
    variant = 'critical';
  } else if (['warning', 'degraded', 'maintenance', 'idle'].includes(normalizedStatus)) {
    variant = 'warning';
  } else if (['paused', 'pending', 'open', 'unclassified'].includes(normalizedStatus)) {
    variant = 'info';
  }
  
  return (
    <Badge variant={variant} className={className}>
      <span className="flex items-center gap-1.5">
        <span
          className={cn(
            'w-1.5 h-1.5 rounded-full',
            variant === 'success' && 'bg-emerald-500',
            variant === 'error' && 'bg-red-500',
            variant === 'warning' && 'bg-amber-500',
            variant === 'info' && 'bg-blue-500',
            variant === 'critical' && 'bg-fuchsia-600',
            variant === 'default' && 'bg-slate-500'
          )}
        />
        <span className="capitalize">{status}</span>
      </span>
    </Badge>
  );
}
