import React from 'react';
import { cn } from '@/lib/utils';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  className?: string;
  label?: string;
  error?: string;
  helperText?: string;
  prefix?: string;
}

export function Input({
  className,
  label,
  error,
  helperText,
  prefix,
  id,
  ...props
}: InputProps) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  
  return (
    <div className="space-y-1">
      {label && (
        <label
          htmlFor={inputId}
          className="block text-sm font-medium text-slate-700"
        >
          {label}
        </label>
      )}
      <div className="relative">
        {prefix ? (
          <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center rounded-l-xl border-r border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 text-sm font-medium text-[var(--text-secondary)]">
            {prefix}
          </span>
        ) : null}
        <input
          id={inputId}
          className={cn(
            "block h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 text-sm text-[var(--text-primary)] shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]",
            prefix && "pl-16",
            error && "border-[var(--tone-danger-border)] focus-visible:ring-[var(--tone-danger-solid)]",
            className
          )}
          {...props}
        />
      </div>
      {error && (
        <p className="text-sm text-red-600">{error}</p>
      )}
      {!error && helperText ? (
        <p className="text-xs text-[var(--text-secondary)]">{helperText}</p>
      ) : null}
    </div>
  );
}

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  className?: string;
  label?: string;
  error?: string;
  helperText?: string;
  options: { value: string; label: string }[];
}

export function Select({
  className,
  label,
  error,
  helperText,
  id,
  options,
  ...props
}: SelectProps) {
  const selectId = id || label?.toLowerCase().replace(/\s+/g, '-');
  
  return (
    <div className="space-y-1">
      {label && (
        <label
          htmlFor={selectId}
          className="block text-sm font-medium text-slate-700"
        >
          {label}
        </label>
      )}
      <select
        id={selectId}
        className={cn(
          "block h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-0)] px-3 text-sm text-[var(--text-primary)] shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]",
          error && "border-[var(--tone-danger-border)] focus-visible:ring-[var(--tone-danger-solid)]",
          className
        )}
        {...props}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {error && (
        <p className="text-sm text-red-600">{error}</p>
      )}
      {!error && helperText ? (
        <p className="text-xs text-[var(--text-secondary)]">{helperText}</p>
      ) : null}
    </div>
  );
}

interface CheckboxProps extends React.InputHTMLAttributes<HTMLInputElement> {
  className?: string;
  label?: string;
}

export function Checkbox({ className, label, id, ...props }: CheckboxProps) {
  const checkboxId = id || label?.toLowerCase().replace(/\s+/g, '-');
  
  return (
    <div className="flex items-center">
      <input
        id={checkboxId}
        type="checkbox"
        className={cn(
          "h-4 w-4 rounded border-[var(--border-subtle)] text-[var(--tone-info-solid)] focus:ring-[var(--focus-ring)]",
          className
        )}
        {...props}
      />
      {label && (
        <label
          htmlFor={checkboxId}
          className="ml-2 block text-sm text-slate-700"
        >
          {label}
        </label>
      )}
    </div>
  );
}
