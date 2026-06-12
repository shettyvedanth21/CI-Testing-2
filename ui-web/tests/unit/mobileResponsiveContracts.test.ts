import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const machinesPageSource = fs.readFileSync(
  path.join(process.cwd(), "app/(protected)/machines/page.tsx"),
  "utf8",
);

const calendarPageSource = fs.readFileSync(
  path.join(process.cwd(), "app/(protected)/calendar/page.tsx"),
  "utf8",
);

test("machines loss breakdown uses stacked mobile cards instead of only desktop table", () => {
  assert.equal(
    machinesPageSource.includes('className="space-y-3 md:hidden"'),
    true,
  );
  assert.equal(
    machinesPageSource.includes('className="hidden overflow-hidden rounded-lg border border-slate-200 md:block"'),
    true,
  );
  assert.equal(
    machinesPageSource.includes('<dt className="text-xs font-medium text-slate-500">Total Loss</dt>'),
    true,
  );
});

test("machines loss drawer is full width on mobile and constrained only from small screens upward", () => {
  assert.equal(
    machinesPageSource.includes('className="absolute right-0 top-0 flex h-full w-full max-w-full flex-col border-l border-[var(--border-subtle)] bg-[var(--surface-0)] shadow-2xl sm:max-w-2xl"'),
    true,
  );
});

test("calendar page builds mobile compact currency labels from compact number formatting", () => {
  assert.equal(
    calendarPageSource.includes("function getCurrencySymbol(currency: string): string"),
    true,
  );
  assert.equal(
    calendarPageSource.includes('notation: "compact"'),
    true,
  );
});

test("calendar mobile cells use tighter badge and taller cell layout for four-digit values", () => {
  assert.equal(
    calendarPageSource.includes('min-h-[6.5rem]'),
    true,
  );
  assert.equal(
    calendarPageSource.includes('className="mt-1.5 flex min-h-8 w-full min-w-0 items-center justify-center'),
    true,
  );
  assert.equal(
    calendarPageSource.includes('text-[10px] font-semibold leading-tight'),
    true,
  );
});
