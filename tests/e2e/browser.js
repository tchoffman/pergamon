// Drives the real pages in headless Chrome as two separate people (separate
// browser contexts = separate localStorage = separate keys).
const puppeteer = require("puppeteer-core");
const BASE = process.env.PERGAMON_URL || "http://127.0.0.1:8096";
const results = [];
const PASSWORD = "pea soup 42";
const check = (name, ok, extra = "") => { results.push({ name, ok }); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : "")); };

async function person(browser, name) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") console.log(`  [${name} console] ${m.text()}`); });
  page.on("pageerror", (e) => console.log(`  [${name} pageerror] ${e.message}`));
  page.on("dialog", (d) => d.accept());
  await page.goto(BASE + "/welcome", { waitUntil: "networkidle0" });
  await page.evaluate(() => { document.getElementById("ac_new_box").open = true; });
  await page.type("#ac_up_name", name);
  await page.type("#ac_up_user", name.toLowerCase());
  await page.type("#ac_up_pass", PASSWORD);
  await page.type("#ac_up_pass2", PASSWORD);
  await page.click("#ac_signup button");
  await page.waitForFunction(() => !document.getElementById("dk_have").classList.contains("hidden"), { timeout: 20000 });
  const npub = await page.$eval("#dk_npub", (n) => n.textContent);
  const user = await page.$eval("#dk_user", (n) => n.textContent);
  check(`${name}: account created on welcome page`, npub.startsWith("npub1") && user === "@" + name.toLowerCase(), npub.slice(0, 16));
  return { page, npub };
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  try {
    const rosa = await person(browser, "Rosa");
    const ben = await person(browser, "Ben");
    const p = rosa.page;

    // Backup key reveal
    await p.evaluate(() => { document.getElementById("dk_backup").closest("details").open = true; });
    await p.click("#dk_backup");
    const nsec = await p.$eval("#dk_nsec_shown", (n) => n.textContent);
    check("backup key shown as nsec", nsec.startsWith("nsec1"));

    // Board
    await p.goto(BASE + "/board", { waitUntil: "networkidle0" });
    const who = await p.$eval("#whoami", (n) => n.textContent);
    check("board says posting as device key", who.includes("Rosa") && who.includes("this device"), who);
    await p.select("#p_cat", "lost-found");
    await p.type("#p_text", "Found: brown dog near the mill");
    await p.click("#postForm button");
    await p.waitForFunction(() => document.getElementById("postResult").textContent.startsWith("posted"), { timeout: 10000 });
    await p.waitForFunction(() => document.getElementById("posts").textContent.includes("brown dog"), { timeout: 10000 });
    check("board post signed in browser and listed", true);

    // Host moderation: this browser is on the hub machine, so it's the host
    await p.goto(BASE + "/board", { waitUntil: "networkidle0" });
    await p.waitForFunction(() => [...document.querySelectorAll("#posts .mod-row button")]
      .some((b) => b.textContent === "Hide"), { timeout: 10000 });
    const before = await p.$$eval("#posts .listing", (n) => n.length);
    await p.evaluate(() => [...document.querySelectorAll("#posts .mod-row button")]
      .find((b) => b.textContent === "Hide").click());
    await p.waitForFunction((n) => document.querySelectorAll("#posts .listing").length === n - 1, {}, before);
    await p.reload({ waitUntil: "networkidle0" });
    await p.waitForFunction(() => !document.getElementById("posts").textContent.includes("Loading"));
    const after = await p.$$eval("#posts .listing", (n) => n.length);
    check("host hides a board post; it stays gone after reload", after === before - 1, `${before} → ${after}`);
    await p.goto(BASE + "/moderation", { waitUntil: "networkidle0" });
    await Promise.all([p.waitForNavigation({ waitUntil: "networkidle0" }),
                       p.evaluate(() => document.querySelector(".undo[data-action=unhide]").click())]);
    await p.goto(BASE + "/board", { waitUntil: "networkidle0" });
    await p.waitForFunction((n) => document.querySelectorAll("#posts .listing").length === n, { timeout: 10000 }, before);
    check("unhide on the Moderation page brings it back", true);

    // Classifieds
    await p.goto(BASE + "/classifieds", { waitUntil: "networkidle0" });
    await p.type("#p_title", "Goat milk");
    await p.type("#p_qty", "1 gallon");
    await p.type("#p_ag", "0.1");
    await p.click("#postForm button");
    await p.waitForFunction(() => document.getElementById("postResult").textContent.startsWith("posted"), { timeout: 10000 });
    await p.waitForFunction(() => document.getElementById("mine").textContent.includes("Goat milk"), { timeout: 10000 });
    check("classified posted from browser, appears in My listings", true);

    // Ben posts a wanted ad that should match Rosa's offer
    const b = ben.page;
    await b.goto(BASE + "/classifieds", { waitUntil: "networkidle0" });
    await b.select("#p_side", "looking-for");
    await b.type("#p_title", "Need goat milk");
    await b.click("#postForm button");
    await b.waitForFunction(() => document.getElementById("postResult").textContent.startsWith("posted"), { timeout: 10000 });
    await b.reload({ waitUntil: "networkidle0" });
    await b.waitForFunction(() => document.getElementById("matches").textContent.includes("Goat milk"), { timeout: 10000 });
    check("Ben's wanted post matches Rosa's offer", true);
    const benMine = await b.$eval("#mine", (n) => n.textContent);
    check("Ben's My listings shows only Ben's posts", benMine.includes("Need goat milk") && !benMine.includes("1 gallon"));

    // Rosa marks hers sold
    await p.reload({ waitUntil: "networkidle0" });
    const buttons = await p.$$("#mine button");
    for (const btn of buttons) {
      if ((await btn.evaluate((n) => n.textContent)).includes("Mark sold")) { await btn.click(); break; }
    }
    await p.waitForFunction(() => document.getElementById("mine").textContent.includes("sold"), { timeout: 10000 });
    check("mark sold re-signed in browser", true);

    // Skills: Ben offers one; Rosa finds it
    await b.goto(BASE + "/skills", { waitUntil: "networkidle0" });
    await b.type("#s_title", "Small engine repair");
    await b.select("#s_cat", "repair");
    await b.type("#s_avail", "weekends");
    await b.click("#postForm button");
    await b.waitForFunction(() => document.getElementById("postResult").textContent.startsWith("posted"), { timeout: 10000 });
    await p.goto(BASE + "/skills", { waitUntil: "networkidle0" });
    await p.type("#q", "engine");
    await p.click("#filterForm button");
    await p.waitForFunction(() => document.getElementById("skills").textContent.includes("Small engine repair"), { timeout: 10000 });
    check("Ben's skill posted from his device and found by Rosa", true);

    // Receipt: Rosa records, Ben confirms
    await p.goto(BASE + "/receipts", { waitUntil: "networkidle0" });
    const myNpub = await p.$eval("#myNpub", (n) => n.textContent);
    check("receipts page shows Rosa's npub", myNpub === rosa.npub);
    await p.type("#d_with", ben.npub);
    await p.type("#d_gave", "1 gallon goat milk");
    await p.type("#d_got", "0.1 oz silver");
    await p.click("#draftForm button");
    await p.waitForSelector("#draftResult svg", { timeout: 10000 });
    const code = await p.$eval("#draftResult .mono", (n) => n.textContent);
    check("receipt recorded with QR + code", code.length > 40);

    await b.goto(BASE + "/receipts", { waitUntil: "networkidle0" });
    await b.type("#c_code", code);
    await b.click("#confirmForm button");
    await b.waitForFunction(() => document.getElementById("confirmResult").textContent.startsWith("Confirmed"), { timeout: 10000 });
    const conf = await b.$eval("#confirmResult", (n) => n.textContent);
    check("Ben confirms from his own key", conf.includes("I gave 0.1 oz silver"), conf);

    await p.reload({ waitUntil: "networkidle0" });
    await p.waitForFunction(() => document.getElementById("mine").textContent.includes("CONFIRMED"), { timeout: 10000 });
    check("Rosa sees the receipt CONFIRMED", true);

    // Reputation shows up on classifieds cards
    await b.goto(BASE + "/classifieds?q=goat", { waitUntil: "networkidle0" });
    const rep = await b.waitForFunction(() => document.body.textContent.includes("confirmed trade"), { timeout: 10000 }).then(() => true).catch(() => false);
    check("reputation line rendered on a card", rep);

    // Food inventory (household-private; localhost is allowed)
    await p.goto(BASE + "/food", { waitUntil: "networkidle0" });
    const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    await p.type("#f_item", "Goat milk");
    await p.type("#f_qty", "2");
    await p.type("#f_unit", "qt");
    await p.select("#f_loc", "fridge");
    await p.$eval("#f_exp", (n, v) => { n.value = v; }, tomorrow);
    await p.click("#addForm button");
    await p.waitForFunction(() => document.getElementById("soon").textContent.includes("Goat milk"), { timeout: 10000 });
    check("food item expiring tomorrow shows under Use soon", true);
    const minus = await p.$("#soon .actions button");
    await minus.click();
    await p.waitForFunction(() => document.getElementById("soon").textContent.includes("1 qt"), { timeout: 10000 });
    check("−1 updates the quantity", true);

    // App shell: home screen → every app → back home in one tap; installable
    await p.goto(BASE + "/", { waitUntil: "networkidle0" });
    const apps = await p.$$eval("a.app", (as) => as.map((a) => a.getAttribute("href")));
    let roundTrips = 0;
    for (const href of apps) {
      await p.goto(BASE + "/", { waitUntil: "networkidle0" });
      await Promise.all([p.waitForNavigation({ waitUntil: "networkidle0" }), p.click(`a.app[href="${href}"]`)]);
      await Promise.all([p.waitForNavigation({ waitUntil: "networkidle0" }), p.click('a[href="/"]')]);
      if ((await p.$$("a.app")).length === apps.length) roundTrips++;
    }
    check(`home → each of ${apps.length} apps → home`, apps.length >= 8 && roundTrips === apps.length,
          `${roundTrips}/${apps.length}`);
    const sw = await p.evaluate(async () => {
      const reg = await navigator.serviceWorker.ready;
      return !!reg.active;
    });
    check("service worker active (installable)", sw);
    const cached = await p.evaluate(async () => !!(await caches.match("/offline")));
    check("offline page cached for when the hub is out of reach", cached);

    // Private messages: Rosa writes to Ben by username; Ben reads and replies
    await p.goto(BASE + "/messages", { waitUntil: "networkidle0" });
    await p.type("#to", "ben");
    await p.type("#text", "Can you trade eggs for honey?");
    await p.click("#compose button");
    await p.waitForFunction(() => document.getElementById("status").textContent === "Sent.", { timeout: 20000 });
    await b.goto(BASE + "/messages", { waitUntil: "networkidle0" });
    await b.waitForFunction(() => document.getElementById("convos").textContent.includes("eggs for honey"), { timeout: 20000 });
    const fromName = await b.$eval("#convos .convo strong", (n) => n.textContent);
    check("Ben gets Rosa's message, shown by her name", fromName === "Rosa", fromName);
    await b.click("#convos .convo");
    await b.type("#text", "Yes, a jar for a dozen");
    await b.click("#compose button");
    await b.waitForFunction(() => document.getElementById("status").textContent === "Sent.", { timeout: 20000 });
    await p.goto(BASE + "/messages", { waitUntil: "networkidle0" });
    await p.waitForFunction(() => document.getElementById("convos").textContent.includes("a jar for a dozen"), { timeout: 20000 });
    check("Rosa sees Ben's reply", true);

    // Local guide: frost dates shown; a place note added and listed
    await p.goto(BASE + "/guide", { waitUntil: "networkidle0" });
    await p.waitForFunction(() => document.getElementById("frostLine").textContent.includes("test data"), { timeout: 10000 });
    check("guide shows the frost dates", true);
    await p.click('.tab[data-tab="places"]');
    await p.type("#nPlace", "Mill Creek");
    await p.select("#nCat", "food");
    await p.type("#nNote", "Watercress grows here in April");
    await p.click("#noteForm button");
    await p.waitForFunction(() => document.getElementById("placeList").textContent.includes("Watercress"), { timeout: 15000 });
    const author = await p.$eval("#placeList .place-note .muted", (n) => n.textContent);
    check("place note added from the browser, shown with its author", author.includes("Rosa"), author);

    // Feeds: subscribe on the Me page
    await p.goto(BASE + "/identity", { waitUntil: "networkidle0" });
    await p.waitForFunction(() => !document.getElementById("feedsBox").classList.contains("hidden"), { timeout: 10000 });
    await p.click('#feedList input[value="news"]');
    await p.click('#feedList input[value="warnings"]');
    await p.click("#feedForm button");
    await p.waitForFunction(() => document.getElementById("feedMsg").textContent.startsWith("Saved"), { timeout: 10000 });
    const saved = await p.$eval("#feedMsg", (n) => n.textContent);
    check("feeds saved on the Me page", saved === "Saved: news, warnings", saved);

    // Sign in as Rosa on a second phone with just username + password
    const ctx2 = await browser.createBrowserContext();
    const p2 = await ctx2.newPage();
    p2.on("dialog", (d) => d.accept());
    await p2.goto(BASE + "/identity", { waitUntil: "networkidle0" });
    await p2.type("#ac_in_user", "rosa");
    await p2.type("#ac_in_pass", "wrong password");
    await p2.click("#ac_signin button");
    await p2.waitForFunction(() => document.getElementById("dk_msg").textContent.includes("Wrong"), { timeout: 20000 });
    check("wrong password refused", true);
    await p2.$eval("#ac_in_pass", (n) => { n.value = ""; });
    await p2.type("#ac_in_pass", PASSWORD);
    await p2.click("#ac_signin button");
    await p2.waitForFunction(() => !document.getElementById("dk_have").classList.contains("hidden"), { timeout: 20000 });
    const npub2 = await p2.$eval("#dk_npub", (n) => n.textContent);
    check("signing in on a second phone gives the same identity", npub2 === rosa.npub);
    await p2.goto(BASE + "/board", { waitUntil: "networkidle0" });
    const who2 = await p2.$eval("#whoami", (n) => n.textContent);
    check("second phone can post as Rosa", who2.includes("Rosa"), who2);

    // Import the backup on a fresh "device"
    const ctx3 = await browser.createBrowserContext();
    const p3 = await ctx3.newPage();
    await p3.goto(BASE + "/welcome", { waitUntil: "networkidle0" });
    await p3.evaluate(() => { document.querySelector("#dk_import").closest("details").open = true; });
    await p3.type("#dk_nsec", nsec);
    await p3.click("#dk_import button");
    await p3.waitForFunction(() => !document.getElementById("dk_have").classList.contains("hidden"));
    const restored = await p3.$eval("#dk_npub", (n) => n.textContent);
    check("restoring backup on a new device gives the same npub", restored === rosa.npub);
  } catch (e) {
    check("unexpected error: " + e.message, false);
  } finally {
    await browser.close();
    const failed = results.filter((r) => !r.ok).length;
    console.log(`\n${results.length - failed}/${results.length} passed`);
    process.exit(failed ? 1 : 0);
  }
})();
