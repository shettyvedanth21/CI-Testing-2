import { createElement } from "react";

import { formatIST } from "../../lib/utils.ts";

type ActivationTimestampFieldProps = {
  label: string;
  timestamp: string | null;
  emptyText?: string;
  className?: string;
  labelClassName?: string;
  valueClassName?: string;
  activeValueClassName?: string;
};

export function ActivationTimestampField({
  label,
  timestamp,
  emptyText = "Not activated yet",
  className = "flex items-center justify-between text-sm",
  labelClassName = "text-slate-500",
  valueClassName = "text-xs text-slate-900",
  activeValueClassName = "inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800 shadow-sm shadow-emerald-100/60",
}: ActivationTimestampFieldProps) {
  const hasTimestamp = Boolean(timestamp);
  const value = formatIST(timestamp, emptyText);

  return createElement(
    "div",
    { className },
    createElement("span", { className: labelClassName }, label),
    createElement(
      "span",
      { className: hasTimestamp ? activeValueClassName : valueClassName },
      hasTimestamp
        ? [
            createElement("span", {
              key: "indicator",
              "aria-hidden": "true",
              className: "h-2 w-2 rounded-full bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.18)]",
            }),
            createElement("span", { key: "value" }, value),
          ]
        : value,
    ),
  );
}
