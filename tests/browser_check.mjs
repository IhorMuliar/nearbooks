// Headless check of site/index.html (optional; needs node and playwright-core with a Chromium build).
//   make site &   then   PLAYWRIGHT_CORE=/path/to/playwright-core/index.mjs [CHROME=/path/to/chrome] \
//                          node tests/browser_check.mjs [http://127.0.0.1:8000/site/] [screenshot.png]
// 1. live: the status badge says live, every attested row is verified (seven browser checks), predictions render.
// 2. offline: with every RPC request aborted, the page shows the "offline data" badge and the cached contract state.
// 3. allowlist: ?rpc=https://evil.example is ignored and no request leaves for a non-allowlisted origin.
// 4. mobile: at 390 px wide the page has no horizontal overflow outside the scrollable tables.
const { chromium } = await import(process.env.PLAYWRIGHT_CORE || "playwright-core");

const BASE = process.argv[2] || "http://127.0.0.1:8000/site/";
const SHOT = process.argv[3];
const ALLOWED = ["127.0.0.1", "rpc.testnet.near.org", "rpc.testnet.fastnear.com", "test.rpc.fastnear.com", "archival-rpc.testnet.near.org", "archival-rpc.testnet.fastnear.com", "testnet-api.outlayer.ai"];
const fail = (m) => { console.log("FAIL " + m); process.exitCode = 1; };
const pass = (m) => console.log("PASS " + m);
const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });

async function open(url, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1200, height: 900 } });
  const page = await ctx.newPage();
  const hosts = new Set();
  page.on("request", (r) => hosts.add(new URL(r.url()).hostname));
  if (opts.offline) await page.route(/rpc\.(testnet\.)?(near\.org|fastnear\.com)|test\.rpc\.fastnear\.com/, (r) => r.abort());
  await page.goto(url);
  await page.waitForFunction(() => !document.querySelector("#status .tag")?.textContent.includes("loading"), null, { timeout: 30000 });
  if (!opts.offline) await page.waitForFunction(() => [...document.querySelectorAll("#att-table td.checks")].every((c) => !c.textContent.includes("checking")), null, { timeout: 30000 });
  return { page, ctx, hosts };
}

{
  const { page, ctx } = await open(BASE);
  const badge = await page.textContent("#status .tag");
  badge === "live" ? pass(`live badge: ${(await page.textContent("#status")).trim()}`) : fail(`badge is ${badge}`);
  const rows = await page.$$eval("#att-table tbody tr", (trs) => trs.map((t) => t.innerText.replace(/\s+/g, " ")));
  const ok = rows.length > 0 && rows.every((r) => ["verified", "known worker ok", "pin ok", "tx ok", "link ok", "wasm ok", "input ok", "output ok"].every((k) => r.toLowerCase().includes(k)) && !r.includes("MISMATCH") && r.includes("tx"));
  ok ? pass(`${rows.length} attested row(s), verified (all seven checks ok): ${rows.join(" | ")}`) : fail(`attested rows: ${rows.join(" | ")}`);
  const preds = await page.$$eval("#preds tbody tr", (trs) => trs.map((t) => t.cells[0].innerText + ":" + t.cells[4].innerText));
  preds.length ? pass(`predictions: ${preds.join(" ")}`) : fail("no predictions");
  const tags = await page.$$eval(".shead .tag", (t) => t.map((x) => x.textContent));
  pass(`section tags: ${tags.join(" / ")}`);
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
  shown.includes("nearbooks-v5.okf-kill-1792497882.testnet") ? pass("?contract= is ignored") : fail("?contract= changed the contract");
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
