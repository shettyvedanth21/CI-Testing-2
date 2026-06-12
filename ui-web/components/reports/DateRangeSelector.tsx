"use client";

import { useState, useEffect, useRef } from "react";
import {
  formatIsoDate,
  getCustomEndDateBoundsWithLimit,
  getMaxSelectableDate,
  getMinSelectableDate,
  getRangeDaySpan,
  getRecentMonths,
  REPORT_DATE_PRESETS,
  resolveCustomEndFromStartWithLimit,
  resolveMonthRange,
  resolvePresetRange,
} from "@/lib/reportDateRange";

interface DateRangeSelectorProps {
  onRangeChange: (start: string, end: string) => void;
  disabled?: boolean;
  initialRange?: { start: string; end: string } | null;
  initialMode?: TabMode;
  maxDays?: number;
  maxDaysMessage?: string;
  onValidationChange?: (isValid: boolean) => void;
}

type TabMode = "presets" | "month" | "custom";

export function DateRangeSelector({
  onRangeChange,
  disabled,
  initialRange,
  initialMode = "presets",
  maxDays = 90,
  maxDaysMessage,
  onValidationChange,
}: DateRangeSelectorProps) {
  const [mode, setMode] = useState<TabMode>(initialMode);
  const [startDate, setStartDate] = useState<string>("");
  const [endDate, setEndDate] = useState<string>("");
  const [selectedMonth, setSelectedMonth] = useState<string>(initialRange?.start || "");
  const lastAppliedInitialRangeRef = useRef<string | null>(null);
  const today = new Date();

  const formatDisplay = (d: Date): string =>
    d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  const presets = REPORT_DATE_PRESETS;
  const months = getRecentMonths(12, today);

  const selectedPresetLabel = presets.find((preset) => {
    const range = resolvePresetRange(preset.days, preset.offset || 0, today);
    return range.start === startDate && range.end === endDate;
  })?.label;

  useEffect(() => {
    if (!initialRange?.start || !initialRange?.end) return;
    const rangeKey = `${initialRange.start}:${initialRange.end}`;
    if (lastAppliedInitialRangeRef.current === rangeKey) return;
    lastAppliedInitialRangeRef.current = rangeKey;
    setStartDate(initialRange.start);
    setEndDate(initialRange.end);
    setSelectedMonth(initialRange.start);
    onRangeChange(initialRange.start, initialRange.end);
  }, [initialRange, onRangeChange]);

  const handlePresetClick = (days: number, offset: number = 0) => {
    const range = resolvePresetRange(days, offset, today);
    setStartDate(range.start);
    setEndDate(range.end);
    onRangeChange(range.start, range.end);
  };

  const handleMonthClick = (monthDate: Date) => {
    const range = resolveMonthRange(monthDate);
    setStartDate(range.start);
    setEndDate(range.end);
    setSelectedMonth(formatIsoDate(monthDate));
    onRangeChange(range.start, range.end);
  };

  const handleCustomStartChange = (value: string) => {
    setStartDate(value);
    const endStr = resolveCustomEndFromStartWithLimit(value, today, maxDays);
    setEndDate(endStr);
    onRangeChange(value, endStr);
  };

  const handleCustomEndChange = (value: string) => {
    setEndDate(value);
    onRangeChange(startDate, value);
  };

  const minDate = getMinSelectableDate(today);
  const maxDate = getMaxSelectableDate(today);
  const customEndBounds = startDate ? getCustomEndDateBoundsWithLimit(startDate, today, maxDays) : null;
  const minEndDate = customEndBounds?.min || "";
  const maxEndDate = customEndBounds?.max || maxDate;
  const selectedDays = getRangeDaySpan(startDate, endDate);
  const isRangeTooLong = selectedDays > maxDays;
  const rangeValidationMessage = maxDaysMessage || `Maximum allowed range is ${maxDays} days.`;

  useEffect(() => {
    onValidationChange?.(!isRangeTooLong);
  }, [isRangeTooLong, onValidationChange]);

  const getRangeSummary = (): string => {
    if (!startDate || !endDate) return "";
    const start = new Date(startDate);
    const end = new Date(endDate);
    return `${formatDisplay(start)} – ${formatDisplay(end)} (${selectedDays} days)`;
  };

  return (
    <div className="space-y-4">
      <div className="w-full overflow-x-auto pb-1">
      <div className="responsive-tab-strip border-b">
        {(["presets", "month", "custom"] as TabMode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            disabled={disabled}
            className={`responsive-tab-link border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              mode === m
                ? "border-blue-600 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {m === "presets" ? "Quick Presets" : m === "month" ? "Month Picker" : "Custom"}
          </button>
        ))}
      </div>
      </div>

      <div className="rounded-lg bg-gray-50 p-4">
        {mode === "presets" && (
          <div className="flex flex-wrap gap-2">
            {presets.map((p) => (
              <button
                key={p.label}
                onClick={() => handlePresetClick(p.days, p.offset || 0)}
                disabled={disabled}
                aria-pressed={selectedPresetLabel === p.label}
                className={`px-3 py-1.5 text-sm border rounded-md transition-colors ${
                  selectedPresetLabel === p.label
                    ? "border-blue-500 bg-blue-50 text-blue-700 shadow-sm"
                    : "bg-white hover:bg-blue-50 hover:border-blue-300"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        )}

        {mode === "month" && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {months.map((m) => (
              <button
                key={m.toISOString()}
                onClick={() => handleMonthClick(m)}
                disabled={disabled}
                className={`px-3 py-2 text-sm bg-white border rounded-md hover:bg-blue-50 hover:border-blue-300 transition-colors ${
                  selectedMonth === formatIsoDate(m) ? "border-blue-500 text-blue-700" : ""
                }`}
              >
                {m.toLocaleDateString("en-GB", { month: "short", year: "2-digit" })}
              </button>
            ))}
          </div>
        )}

        {mode === "custom" && (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Start Date</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => handleCustomStartChange(e.target.value)}
                min={minDate}
                max={maxDate}
                disabled={disabled}
                className="w-full px-3 py-2 border rounded-md text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">End Date</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => handleCustomEndChange(e.target.value)}
                min={minEndDate}
                max={maxEndDate}
                disabled={disabled}
                className="w-full px-3 py-2 border rounded-md text-sm"
              />
            </div>
            {startDate && endDate && (
              <p className={`text-sm ${isRangeTooLong ? "text-red-600" : "text-gray-600"}`}>{selectedDays} days selected</p>
            )}
            {isRangeTooLong ? (
              <p className="text-sm font-medium text-red-600">{rangeValidationMessage}</p>
            ) : null}
          </div>
        )}
      </div>

      {startDate && endDate && (
        <div className={`text-sm p-3 rounded-md ${isRangeTooLong ? "bg-red-50 text-red-700" : "bg-blue-50 text-gray-600"}`}>
          Selected: <strong>{getRangeSummary()}</strong>
        </div>
      )}
    </div>
  );
}
