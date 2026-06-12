import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const wastePagePath = path.resolve(__dirname, "../../app/(protected)/waste-analysis/page.tsx");
const energyPagePath = path.resolve(__dirname, "../../app/(protected)/reports/energy/page.tsx");
const selectorPath = path.resolve(__dirname, "../../components/reports/DateRangeSelector.tsx");

const wastePageSource = readFileSync(wastePagePath, "utf-8");
const energyPageSource = readFileSync(energyPagePath, "utf-8");
const selectorSource = readFileSync(selectorPath, "utf-8");

test("waste analysis uses shared report DateRangeSelector", () => {
  assert.equal(wastePageSource.includes('import { DateRangeSelector } from "@/components/reports/DateRangeSelector";'), true);
  assert.equal(wastePageSource.includes("<DateRangeSelector"), true);
});

test("energy report keeps using shared report DateRangeSelector", () => {
  assert.equal(energyPageSource.includes('import { DateRangeSelector } from "@/components/reports/DateRangeSelector";'), true);
  assert.equal(energyPageSource.includes("<DateRangeSelector"), true);
});

test("waste analysis no longer renders separate Start Date and End Date inputs", () => {
  assert.equal(wastePageSource.includes(">Start Date<"), false);
  assert.equal(wastePageSource.includes(">End Date<"), false);
  assert.equal(wastePageSource.includes("<input type=\"date\" value={startDate}"), false);
  assert.equal(wastePageSource.includes("<input type=\"date\" value={endDate}"), false);
});

test("waste analysis derives its default range on client mount instead of server memoization", () => {
  assert.equal(wastePageSource.includes("useMemo(() => getWasteDefaultRange(), [])"), false);
  assert.equal(wastePageSource.includes("useEffect(() => {\n    const range = getWasteDefaultRange();"), true);
  assert.equal(wastePageSource.includes("const [defaultRange, setDefaultRange] = useState<{ start: string; end: string } | null>(null);"), true);
});

test("DateRangeSelector only reapplies initialRange when the actual range value changes", () => {
  assert.equal(selectorSource.includes("const lastAppliedInitialRangeRef = useRef<string | null>(null);"), true);
  assert.equal(selectorSource.includes("const rangeKey = `${initialRange.start}:${initialRange.end}`;"), true);
  assert.equal(selectorSource.includes("if (lastAppliedInitialRangeRef.current === rangeKey) return;"), true);
});

test("DateRangeSelector supports configurable max-day validation", () => {
  assert.equal(selectorSource.includes("maxDays?: number;"), true);
  assert.equal(selectorSource.includes("maxDaysMessage?: string;"), true);
  assert.equal(selectorSource.includes("onValidationChange?: (isValid: boolean) => void;"), true);
  assert.equal(selectorSource.includes("Maximum allowed range is"), true);
});
