import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

function parseUtcTimestamp(value: string): Date {
  const ts = value.trim();
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(ts);
  return new Date(hasTimezone ? ts : `${ts}Z`);
}

export function formatIST(
  utcTimestamp: string | null,
  emptyText = 'No data received'
): string {
  if (!utcTimestamp) return emptyText;
  try {
    const date = parseUtcTimestamp(utcTimestamp);
    if (isNaN(date.getTime())) return 'Invalid date';
    return (
      date.toLocaleString('en-IN', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
        timeZone: 'Asia/Kolkata',
      }) + ' IST'
    );
  } catch {
    return 'Invalid date';
  }
}

export function formatISTCompact(
  utcTimestamp: string | null,
  emptyText = 'No data received'
): string {
  if (!utcTimestamp) return emptyText;
  try {
    const date = parseUtcTimestamp(utcTimestamp);
    if (isNaN(date.getTime())) return 'Invalid date';
    return date.toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
      timeZone: 'Asia/Kolkata',
    });
  } catch {
    return 'Invalid date';
  }
}

export function getRelativeTime(utcTimestamp: string | null): string {
  if (!utcTimestamp) return '';
  
  try {
    const date = new Date(utcTimestamp);
    if (isNaN(date.getTime())) return '';
    
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    
    if (diffSec < 5) return '(just now)';
    if (diffSec < 60) return `(${diffSec} seconds ago)`;
    if (diffMin < 60) return `(${diffMin} minute${diffMin > 1 ? 's' : ''} ago)`;
    if (diffHour < 24) return `(${diffHour} hour${diffHour > 1 ? 's' : ''} ago)`;
    return '';
  } catch {
    return '';
  }
}
