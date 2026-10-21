// Headless check of site/index.html (optional; needs node and playwright-core with a Chromium build).
//   make site &   then   PLAYWRIGHT_CORE=/path/to/playwright-core/index.mjs [CHROME=/path/to/chrome] \
//                          node tests/browser_check.mjs [http://127.0.0.1:8000/site/] [screenshot.png]
// 1. live: the status badge says live, every attested row is verified (seven browser checks), the latest entry shows 7 of 7, predictions render.
// 2. offline: with every RPC request aborted, the page shows the "offline data" badge and the cached contract state.
// 3. allowlist: ?rpc=https://evil.example is ignored and no request leaves for a non-allowlisted origin.
// 4. mobile: at 390 px wide the page has no horizontal overflow outside the scrollable tables.
const { chromium } = await import(process.env.PLAYWRIGHT_CORE || "playwright-core");

const BASE = process.argv[2] || "http://127.0.0.1:8000/site/";
const SHOT = process.argv[3];
const ALLOWED = [new URL(BASE).hostname, "free.rpc.fastnear.com", "rpc.mainnet.near.org", "archival-rpc.mainnet.fastnear.com", "archival-rpc.mainnet.near.org", "api.outlayer.ai"];
const fail = (m) => { console.log("FAIL " + m); process.exitCode = 1; };
const pass = (m) => console.log("PASS " + m);
const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });

async function open(url, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1200, height: 900 } });
  const page = await ctx.newPage();
  const hosts = new Set();
  page.on("request", (r) => hosts.add(new URL(r.url()).hostname));
  if (opts.offline) await page.route(/rpc\.(mainnet\.)?(near\.org|fastnear\.com)/, (r) => r.abort());
  await page.goto(url);
  await page.waitForFunction(() => !document.querySelector("#status .tag")?.textContent.includes("loading"), null, { timeout: 30000 });
  if (!opts.offline) await page.waitForFunction(() => document.querySelector("#att-table tbody tr") && !document.querySelector('#att-table tr[data-verdict="pending"]'), null, { timeout: 30000 });
  return { page, ctx, hosts };
}

{
  const { page, ctx } = await open(BASE);
  const badge = await page.textContent("#status .tag");
  badge === "live" ? pass(`live badge: ${(await page.textContent("#status")).trim()}`) : fail(`badge is ${badge}`);
  const rows = await page.$$eval("#att-table tbody tr", (trs) => trs.map((t) => ({ text: t.innerText.replace(/\s+/g, " "), verdict: t.dataset.verdict,
    checks: [...t.querySelectorAll("[data-check]")].map((c) => c.dataset.check + "=" + c.dataset.ok), tx: !!t.querySelector("a[data-tx]") })));
  const ok = rows.length > 0 && rows.every((r) => r.verdict === "verified" && r.checks.length === 7 && r.checks.every((c) => c.endsWith("=true")) && r.tx);
  ok ? pass(`${rows.length} attested row(s), verified (all seven checks ok, tx linked): ${rows.map((r) => r.text.slice(0, 60) + " [" + r.checks.join(" ") + "]").join(" | ")}`) : fail(`attested rows: ${JSON.stringify(rows)}`);
  const entry = await page.evaluate(() => ({ verdict: document.getElementById("entry-verdict").dataset.verdict, ticks: document.querySelectorAll('#entry-checks [data-ok="true"]').length, h1: document.getElementById("top").textContent }));
  entry.verdict === "verified" && entry.ticks === 7 ? pass(`latest entry verified, 7 of 7 marked; title: ${entry.h1}`) : fail(`latest entry: ${JSON.stringify(entry)}`);
  const preds = await page.$$eval("#preds [data-pred]", (ps) => ps.map((p) => p.dataset.pred + ":" + p.dataset.status));
  preds.length ? pass(`predictions: ${preds.join(" ")}`) : fail("no predictions");
  const claims = await page.$$eval("#claims li", (l) => l.map((x) => x.querySelector(".src")?.textContent));
  claims.length >= 3 && claims.every(Boolean) ? pass(`main claims, each marked with its source: ${claims.join(" / ")}`) : fail(`claims: ${claims}`);
  if (SHOT) { await page.screenshot({ path: SHOT, fullPage: true }); pass(`screenshot ${SHOT}`); }
  await ctx.close();
}
{
  const { page, ctx } = await open(BASE, { offline: true });
  const badge = await page.textContent("#status .tag");
  const n = await page.$$eval("#att-table tbody tr", (t) => t.length);
  badge === "offline data" && n > 0 ? pass(`offline: ${(await page.textContent("#status")).trim().slice(0, 160)}`) : fail(`offline badge ${badge}, ${n} rows`);
  await ctx.close();
}
{
  const { page, ctx, hosts } = await open(BASE + "?rpc=https://evil.example&contract=attacker.testnet");
  const shown = await page.textContent("#status");
  shown.includes("nearbooks.near") && !shown.includes("attacker") ? pass("?contract= is ignored") : fail("?contract= changed the contract");
  const bad = [...hosts].filter((h) => !ALLOWED.includes(h));
  bad.length ? fail(`requests to non-allowlisted hosts: ${bad}`) : pass(`hosts contacted: ${[...hosts].join(", ")}`);
  await ctx.close();
}
{
  const { page, ctx } = await open(BASE, { viewport: { width: 390, height: 844 } });
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  over <= 0 ? pass("390 px wide: no page-level horizontal overflow") : fail(`page overflows by ${over}px at 390 px`);
  await ctx.close();
}
await browser.close();
