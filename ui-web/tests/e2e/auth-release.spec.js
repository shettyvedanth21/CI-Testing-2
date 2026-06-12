const { test, expect } = require("@playwright/test");

const BASE = process.env.UI_WEB_BASE_URL ?? "http://127.0.0.1:3000";
const SCREENSHOT_DIR = "/var/folders/62/231ktlsx34b_jc5d1x9krq7h0000gn/T/opencode/e2e-screenshots";

const ADMIN_EMAIL = "manash.ray@cittagent.com";
const ADMIN_PASSWORD = "Shivex@2706!";

const protectedRoutes = [
  { name: "machines", path: "/machines" },
  { name: "machine-detail", path: "/machines/AD00000001" },
  { name: "calendar", path: "/calendar" },
  { name: "analytics", path: "/analytics" },
  { name: "reports", path: "/reports" },
  { name: "waste-analysis", path: "/waste-analysis" },
  { name: "rules", path: "/rules" },
  { name: "copilot", path: "/copilot" },
];

const results = {};

test.describe("Authenticated release validation", () => {
  let authedContext;

  test.beforeAll(async ({ browser }) => {
    authedContext = await browser.newContext();
    const page = await authedContext.newPage();

    await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 30000 });

    const emailInput = page.locator('input[type="email"], input[name="email"], input[placeholder*="email" i], input[placeholder*="Email" i]');
    const passInput = page.locator('input[type="password"], input[name="password"]');
    const submitBtn = page.locator('button[type="submit"], button:has-text("Sign in"), button:has-text("Login"), button:has-text("Log in")');

    const hasEmailInput = await emailInput.count();
    if (hasEmailInput > 0) {
      await emailInput.first().fill(ADMIN_EMAIL);
      await passInput.first().fill(ADMIN_PASSWORD);
      await submitBtn.first().click();
      await page.waitForTimeout(5000);
    }

    const token = await page.evaluate(() => window.sessionStorage.getItem("factoryops_access_token_v2"));
    const onProtectedRoute = page.url().includes("/machines") || page.url().includes("/admin") || page.url().includes("/dashboard");
    const currentUrl = page.url();

    console.log(`AUTH RESULT: url=${currentUrl}, has_token=${!!token}, token_len=${token?.length ?? 0}, on_protected=${onProtectedRoute}`);

    if (!token) {
      const altToken = await page.evaluate(() => window.sessionStorage.getItem("factoryops_access_token"));
      console.log(`ALT TOKEN: has=${!!altToken}, len=${altToken?.length ?? 0}`);
    }

    await page.close();
  });

  for (const route of protectedRoutes) {
    test(`${route.name} — authenticated validation`, async () => {
      const page = await authedContext.newPage();
      const consoleErrors = [];
      const failedRequests = [];
      let http5xxCount = 0;

      page.on("console", (msg) => {
        if (msg.type() === "error") consoleErrors.push(msg.text().substring(0, 300));
      });
      page.on("requestfailed", (req) => {
        failedRequests.push({ url: req.url().substring(0, 200), err: req.failure()?.errorText?.substring(0, 150) });
      });
      page.on("response", (resp) => {
        if (resp.status() >= 500) http5xxCount++;
      });

      const url = `${BASE}${route.path}`;
      const response = await page.goto(url, { waitUntil: "networkidle", timeout: 30000 }).catch(() => null);

      await page.waitForTimeout(5000);

      const finalUrl = page.url();
      const redirectedToLogin = finalUrl.includes("/login");

      const token = await page.evaluate(() => window.sessionStorage.getItem("factoryops_access_token_v2"));

      const bodyText = await page.locator("body").innerText();
      const isBlank = bodyText.trim().length < 10;
      const title = await page.title();

      const idx = protectedRoutes.indexOf(route) + 1;
      const ssName = `auth-${String(idx).padStart(2, "0")}-${route.name}.png`;
      await page.screenshot({ path: `${SCREENSHOT_DIR}/${ssName}`, fullPage: true });

      const summary = {
        surface: route.name,
        path: route.path,
        http_status: response?.status() ?? "no_response",
        final_url: finalUrl,
        title,
        authenticated: !!token,
        redirectedToLogin,
        isBlank,
        consoleErrors: consoleErrors.length,
        consoleErrorSamples: consoleErrors.filter(e => !e.includes("401") && !e.includes("forgot-password")).slice(0, 5),
        allConsoleErrors: consoleErrors.slice(0, 8),
        failedRequests: failedRequests.length,
        failedRequestSamples: failedRequests.filter(r => !r.url.includes("forgot-password") && !r.url.includes("_rsc")).slice(0, 5),
        allFailedRequests: failedRequests.slice(0, 8),
        http5xx: http5xxCount,
        screenshot: ssName,
        PASS: !!token && !redirectedToLogin && !isBlank && http5xxCount === 0,
      };

      results[route.name] = summary;

      console.log(`${summary.PASS ? "PASS" : "FAIL"} ${route.name}: auth=${!!token}, redirect_login=${redirectedToLogin}, blank=${isBlank}, http=${response?.status()}, 5xx=${http5xxCount}, errors=${consoleErrors.length}, failed_reqs=${failedRequests.length}`);

      await page.close();
    });
  }

  test.afterAll(async () => {
    if (authedContext) await authedContext.close();
    console.log("\n=== FINAL AUTHENTICATED SUMMARY ===");
    let allPass = true;
    for (const [name, r] of Object.entries(results)) {
      const status = r.PASS ? "PASS" : "FAIL";
      if (!r.PASS) allPass = false;
      console.log(`${status} ${name}: auth=${r.authenticated}, redirect_login=${r.redirectedToLogin}, blank=${r.isBlank}, http=${r.http_status}, 5xx=${r.http5xx}, console_errs=${r.consoleErrors}, failed_reqs=${r.failedRequests}`);
      if (r.allConsoleErrors.length > 0) console.log(`  ALL console errors: ${JSON.stringify(r.allConsoleErrors)}`);
      if (r.allFailedRequests.length > 0) console.log(`  ALL failed reqs: ${JSON.stringify(r.allFailedRequests)}`);
    }
    console.log(`\nOVERALL: ${allPass ? "ALL PASS" : "SOME FAIL"}`);
  });
});
