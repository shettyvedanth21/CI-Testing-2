export interface DateRangeValue {
  start: string;
  end: string;
}

export interface DatePreset {
  label: string;
  days: number;
  offset?: number;
}

export const REPORT_DATE_PRESETS: DatePreset[] = [
  { label: "Today", days: 1 },
  { label: "Yesterday", days: 1, offset: 1 },
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
];

function localDate(year: number, month: number, day: number): Date {
  return new Date(year, month, day);
}

function startOfLocalDay(date: Date): Date {
  return localDate(date.getFullYear(), date.getMonth(), date.getDate());
}

export function formatIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function resolvePresetRange(days: number, offset: number = 0, now: Date = new Date()): DateRangeValue {
  const end = startOfLocalDay(now);
  end.setDate(end.getDate() - offset);
  const inclusiveSpan = Math.max(days - 1, 0);
  const start = new Date(end);
  start.setDate(start.getDate() - inclusiveSpan);
  return {
    start: formatIsoDate(start),
    end: formatIsoDate(end),
  };
}

export function resolveMonthRange(monthDate: Date): DateRangeValue {
  const start = localDate(monthDate.getFullYear(), monthDate.getMonth(), 1);
  const end = localDate(monthDate.getFullYear(), monthDate.getMonth() + 1, 0);
  return {
    start: formatIsoDate(start),
    end: formatIsoDate(end),
  };
}

export function getRecentMonths(count: number = 12, now: Date = new Date()): Date[] {
  const months: Date[] = [];
  for (let i = 0; i < count; i++) {
    months.push(new Date(now.getFullYear(), now.getMonth() - i, 1));
  }
  return months;
}

export function resolveYesterday(now: Date = new Date()): Date {
  const yesterday = startOfLocalDay(now);
  yesterday.setDate(yesterday.getDate() - 1);
  return yesterday;
}

export function resolveCustomEndFromStart(startDate: string, now: Date = new Date()): string {
  const start = new Date(`${startDate}T00:00:00`);
  let end = new Date(start);
  end.setDate(end.getDate() + 89);
  const today = startOfLocalDay(now);
  if (end > today) {
    end = today;
  }
  return formatIsoDate(end);
}

export function getMinSelectableDate(now: Date = new Date()): string {
  return formatIsoDate(localDate(now.getFullYear() - 1, now.getMonth(), now.getDate()));
}

export function getMaxSelectableDate(now: Date = new Date()): string {
  return formatIsoDate(startOfLocalDay(now));
}

export function getCustomEndDateBounds(startDate: string, now: Date = new Date()): { min: string; max: string } {
  const start = new Date(`${startDate}T00:00:00`);
  const min = start;
  const today = startOfLocalDay(now);
  const max = new Date(
    Math.min(
      start.getTime() + 89 * 24 * 60 * 60 * 1000,
      today.getTime(),
    ),
  );
  return {
    min: formatIsoDate(min),
    max: formatIsoDate(max),
  };
}

export function getRangeDaySpan(startDate: string, endDate: string): number {
  if (!startDate || !endDate) return 0;
  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  const diff = end.getTime() - start.getTime();
  if (!Number.isFinite(diff) || diff < 0) return 0;
  return Math.floor(diff / (1000 * 60 * 60 * 24)) + 1;
}

export function resolveCustomEndFromStartWithLimit(
  startDate: string,
  now: Date = new Date(),
  maxDays: number = 90,
): string {
  const start = new Date(`${startDate}T00:00:00`);
  let end = new Date(start);
  end.setDate(end.getDate() + Math.max(0, maxDays - 1));
  const today = startOfLocalDay(now);
  if (end > today) {
    end = today;
  }
  return formatIsoDate(end);
}

export function getCustomEndDateBoundsWithLimit(
  startDate: string,
  now: Date = new Date(),
  maxDays: number = 90,
): { min: string; max: string } {
  const start = new Date(`${startDate}T00:00:00`);
  const min = start;
  const today = startOfLocalDay(now);
  const max = new Date(
    Math.min(
      start.getTime() + Math.max(0, maxDays - 1) * 24 * 60 * 60 * 1000,
      today.getTime(),
    ),
  );
  return {
    min: formatIsoDate(min),
    max: formatIsoDate(max),
  };
}

export function getWasteDefaultRange(now: Date = new Date()): DateRangeValue {
  const end = startOfLocalDay(now);
  const start = new Date(end);
  start.setDate(start.getDate() - 6);
  return {
    start: formatIsoDate(start),
    end: formatIsoDate(end),
  };
}
