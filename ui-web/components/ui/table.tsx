import React from 'react';
import { cn } from '@/lib/utils';

interface TableProps extends React.TableHTMLAttributes<HTMLTableElement> {
  children: React.ReactNode;
  className?: string;
}

export function Table({ children, className, ...props }: TableProps) {
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border-subtle)]">
      <table
        className={cn("min-w-full divide-y divide-[var(--border-subtle)]", className)}
        {...props}
      >
        {children}
      </table>
    </div>
  );
}

interface TableHeaderProps extends React.HTMLAttributes<HTMLTableSectionElement> {
  children: React.ReactNode;
  className?: string;
}

export function TableHeader({ children, className, ...props }: TableHeaderProps) {
  return (
    <thead className={cn("bg-[var(--surface-1)]", className)} {...props}>
      {children}
    </thead>
  );
}

interface TableBodyProps extends React.HTMLAttributes<HTMLTableSectionElement> {
  children: React.ReactNode;
  className?: string;
}

export function TableBody({ children, className, ...props }: TableBodyProps) {
  return (
    <tbody
      className={cn("divide-y divide-[var(--border-subtle)] bg-[var(--surface-0)]", className)}
      {...props}
    >
      {children}
    </tbody>
  );
}

interface TableRowProps extends React.HTMLAttributes<HTMLTableRowElement> {
  children: React.ReactNode;
  className?: string;
}

export function TableRow({ children, className, ...props }: TableRowProps) {
  return (
    <tr className={cn("transition-colors hover:bg-[var(--surface-1)]", className)} {...props}>
      {children}
    </tr>
  );
}

interface TableHeadProps extends React.ThHTMLAttributes<HTMLTableCellElement> {
  children: React.ReactNode;
  className?: string;
}

export function TableHead({ children, className, ...props }: TableHeadProps) {
  return (
    <th
      className={cn(
        "px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]",
        className
      )}
      {...props}
    >
      {children}
    </th>
  );
}

interface TableCellProps extends React.TdHTMLAttributes<HTMLTableCellElement> {
  children: React.ReactNode;
  className?: string;
}

export function TableCell({ children, className, ...props }: TableCellProps) {
  return (
    <td
      className={cn("whitespace-nowrap px-4 py-3 text-sm text-[var(--text-primary)]", className)}
      {...props}
    >
      {children}
    </td>
  );
}
