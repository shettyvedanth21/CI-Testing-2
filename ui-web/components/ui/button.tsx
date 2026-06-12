import React from 'react';
import { cn } from '@/lib/utils';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  children: React.ReactNode;
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  isLoading?: boolean;
}

const variantStyles = {
  primary:
    "border-transparent bg-[linear-gradient(135deg,#0ea5e9,#2563eb)] text-white shadow-sm hover:brightness-105",
  secondary: "border-transparent bg-[var(--surface-2)] text-[var(--text-primary)] hover:bg-[var(--surface-1)]",
  outline: "border-[var(--border-subtle)] bg-transparent text-[var(--text-secondary)] hover:bg-[var(--surface-1)]",
  ghost: "border-transparent bg-transparent text-[var(--text-secondary)] hover:bg-[var(--surface-1)]",
  danger: "border-transparent bg-[linear-gradient(135deg,#ef4444,#dc2626)] text-white shadow-sm hover:brightness-105",
};

const sizeStyles = {
  sm: "h-8 px-3 text-xs",
  md: "h-10 px-4 text-sm",
  lg: "h-11 px-5 text-sm",
};

export function Button({
  children,
  variant = 'primary',
  size = 'md',
  className,
  isLoading = false,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded-xl border font-semibold tracking-[-0.01em] transition duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-55",
        variantStyles[variant],
        sizeStyles[size],
        className
      )}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading && (
        <svg
          className="animate-spin -ml-1 mr-2 h-4 w-4 text-current"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
          />
        </svg>
      )}
      {children}
    </button>
  );
}
