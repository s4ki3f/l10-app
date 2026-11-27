import { expect, firefox, request, test, type Browser, type Page } from "@playwright/test";

/**
 * Brief minimum: one Playwright test with two browsers — the facilitator advances the agenda and the
 * second browser follows. Literally two browsers: the facilitator in Chromium (the project's browser),
 * the follower in a separately launched Firefox — two processes, two engines, separate cookies.
 *
 * E2E_BREAK_SSE=1 blocks the follower's live stream. That run must fail: it produced the committed
 * failure trace (docs/artifacts), which shows the test catches a broken real-time path.
 */
const FOLLOW_MS = 2_000; // brief: "within about a second"; 2 s leaves room for a cold dev server

async function signIn(browser: Browser, userId: string): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  await page.goto("/login");
  await page.getByTestId(`signin-${userId}`).click();
  await expect(page).toHaveURL(/\/teams\/team_lead$/);
  return page;
}

const section = (page: Page) => page.getByTestId("meeting-bar");

test("facilitator advances the agenda and the second browser follows", async ({ browser }) => {
  const second = await firefox.launch(); // the second browser
  test.info().annotations.push({ type: "browsers", description: `${browser.browserType().name()} + ${second.browserType().name()}` });
  const facilitator = await signIn(browser, "u3"); // Casey, Team Member — in Chromium
  const follower = await signIn(second, "u2"); // Blake, Team Member — in Firefox
  // The config traces every context created in the test, the Firefox one included.
  try {

    // Facilitator starts the meeting from the Team page.
    await facilitator.getByTestId("open-meeting").click();
    await facilitator.getByTestId("start-meeting").click();
    await expect(section(facilitator)).toHaveAttribute("data-section", "scorecard");
    await expect(facilitator.getByTestId("advance")).toBeVisible(); // only the facilitator has controls

    // The follower sees the meeting appear (live banner) and joins from their own browser.
    await follower.getByTestId("join-meeting").click({ timeout: FOLLOW_MS * 2 });
    await expect(section(follower)).toHaveAttribute("data-section", "scorecard");
    await expect(follower.getByTestId("advance")).toHaveCount(0);
    await expect(follower.getByTestId("attendees")).toContainText("Casey Cho (facilitator)");
    await expect(facilitator.getByTestId("attendees")).toContainText("Blake Brooks", { timeout: FOLLOW_MS });

    if (process.env.E2E_BREAK_SSE) {
      // Sabotage for the failure artifact: cut the follower's live stream after they've joined.
      await follower.route("**/api/teams/*/events", (route) => route.abort());
      await follower.reload();
      await expect(section(follower)).toHaveAttribute("data-section", "scorecard");
    }

    // Facilitator advances: Scorecard → IDS → Conclude. The follower's screen follows each time.
    for (const [next, title] of [["ids", "IDS"], ["conclude", "Conclude"]] as const) {
      await facilitator.getByTestId("advance").click();
      await expect(section(follower)).toHaveAttribute("data-section", next, { timeout: FOLLOW_MS });
      await expect(follower.getByTestId("current-section")).toHaveText(title);
    }

    // Both rate; the facilitator sees the follower's rating arrive.
    await follower.getByTestId("rate-8").click();
    await facilitator.getByTestId("rate-9").click();
    await expect(facilitator.getByText("Average so far: 8.5")).toBeVisible({ timeout: FOLLOW_MS });

    // Facilitator ends it; the follower lands on the summary.
    await facilitator.getByTestId("end-meeting").click();
    await facilitator.getByTestId("confirm-end").click();
    await expect(follower.getByTestId("summary")).toBeVisible({ timeout: FOLLOW_MS });
    await expect(follower.getByTestId("summary-rating")).toHaveText("8.5");
  } finally {
    await follower.context().close(); // closing the context lets Playwright keep its trace on failure
    await second.close();
  }
});

test("a refresh mid-meeting recovers the current section and timer", async ({ browser }) => {
  const facilitator = await signIn(browser, "u4"); // Dana
  await facilitator.getByTestId("open-meeting").click();
  await facilitator.getByTestId("start-meeting").click();
  await facilitator.getByTestId("advance").click();
  await expect(section(facilitator)).toHaveAttribute("data-section", "ids");
  await facilitator.waitForTimeout(2_100);

  await facilitator.reload();
  await expect(section(facilitator)).toHaveAttribute("data-section", "ids");
  await expect(facilitator.getByTestId("advance")).toBeVisible(); // control is tied to the user, not the tab
  await expect(facilitator.getByTestId("timer")).not.toHaveText("0:00"); // the server-owned timer kept running

  await facilitator.getByTestId("end-meeting").click();
  await facilitator.getByTestId("confirm-end").click();
  await expect(facilitator.getByTestId("summary")).toBeVisible();
});

test("an observer follows read-only", async ({ browser }) => {
  const observer = await signIn(browser, "u5"); // Ellis, Observer
  await expect(observer.getByText("Read only", { exact: true })).toBeVisible(); // header badge
  await expect(observer.getByTestId("open-meeting")).toHaveCount(0); // can't start
  await expect(observer.getByRole("button", { name: "Solve" })).toHaveCount(0);
  await expect(observer.getByTestId("cell-m1-2026-11-02")).toHaveAttribute("data-status", "off_track");
});

test("a change made while the browser is still connecting is not lost (listen, then fetch)", async ({ browser, playwright }) => {
  // Hold the follower's live stream back so its initial fetch finishes first, then commit a change
  // inside that gap. The browser must still show it once the stream says hello.
  const ctx = await browser.newContext();
  await ctx.route("**/api/teams/*/events", async (route) => {
    await new Promise((r) => setTimeout(r, 2_500));
    await route.continue();
  });
  const follower = await ctx.newPage();
  await follower.goto("/login");
  await follower.getByTestId("signin-u2").click();
  await expect(follower.getByTestId("issues-open")).toBeVisible(); // initial fetch done, stream not yet open
  await expect(follower.getByTestId("live-status")).not.toHaveAttribute("data-status", "live");

  const title = `Raised while you were connecting ${Date.now()}`;
  const casey = await playwright.request.newContext({ baseURL: test.info().project.use.baseURL });
  expect((await casey.post("/api/auth/login", { data: { user_id: "u3" } })).ok()).toBeTruthy();
  expect((await casey.post("/api/teams/team_lead/issues", { data: { title } })).status()).toBe(201);

  await expect(follower.getByTestId("live-status")).toHaveAttribute("data-status", "live", { timeout: 6_000 });
  // no other change will arrive to rescue it (scoped: the Activity feed shows the title too)
  await expect(follower.getByTestId("issues-open").getByText(title)).toBeVisible({ timeout: 2_000 });
});

test("bonus: a stale score edit is refused instead of overwriting someone else (two browsers)", async ({ browser }) => {
  const second = await firefox.launch();
  const casey = await signIn(browser, "u3"); // Chromium
  const blake = await signIn(second, "u2"); // Firefox
  try {
    const cellButton = (p: Page) => p.getByRole("button", { name: /^Customer churn, week of Nov 2:/ });
    // Values unique to this run: never the seed's 3, never what an earlier run (or another browser project) left.
    const shown = (await cellButton(casey).getAttribute("aria-label"))!.match(/: ([\d.]+) —/)?.[1];
    let mine = (1 + (Date.now() % 800) / 100).toFixed(2);
    if (mine === shown) mine = (Number(mine) + 0.01).toFixed(2);
    const blakes = mine;
    const caseys = (Number(blakes) + 0.37).toFixed(2);

    // Casey starts editing what she sees…
    await cellButton(casey).click();
    // …meanwhile Blake changes the same cell and saves.
    await cellButton(blake).click();
    await blake.keyboard.press("ControlOrMeta+a");
    await blake.keyboard.type(blakes);
    await blake.keyboard.press("Enter");
    await expect(cellButton(blake)).toContainText(String(Number(blakes)));
    // Casey's screen refreshes live under her open editor, but she saves what she typed against what she saw.
    await casey.keyboard.press("ControlOrMeta+a");
    await casey.keyboard.type(caseys);
    await casey.keyboard.press("Enter");

    const b = String(Number(blakes));
    await expect(casey.getByText(`Blake Brooks changed this to ${b} a moment ago. Your ${caseys} wasn't saved.`)).toBeVisible();
    await expect(cellButton(casey)).toContainText(b); // Blake's change survived
    await expect(cellButton(blake)).toContainText(b);
  } finally {
    await blake.context().close();
    await second.close();
  }
});

/** Self-contained bonus tests: solve any open Issue on the item first, so the button must be there this run. */
async function freshItems(base: string, items: { rocks?: string[]; todos?: string[] }) {
  const api = await request.newContext({ baseURL: base });
  await api.post("/api/auth/login", { data: { user_id: "u3" } });
  const rocks = await (await api.get("/api/teams/team_lead/rocks")).json();
  const todos = (await (await api.get("/api/teams/team_lead/todos")).json()).items;
  const open = [
    ...rocks.filter((r: { id: string }) => items.rocks?.includes(r.id)),
    ...todos.filter((t: { id: string }) => items.todos?.includes(t.id)),
  ].map((x: { open_issue_id: string | null }) => x.open_issue_id).filter(Boolean);
  for (const id of open) await api.post(`/api/issues/${id}/solve`, { data: { note: "cleared by the E2E test" } });
  await api.dispose();
}

test("bonus: an off-track Rock and an overdue To-Do become Issues; another browser sees them live", async ({ browser, baseURL }) => {
  await freshItems(baseURL!, { rocks: ["r2"], todos: ["t31"] });
  const second = await firefox.launch();
  const casey = await signIn(browser, "u3");
  const blake = await signIn(second, "u2");
  try {
    // Blake is live and sees the buttons BEFORE anything happens, so what follows is a live update, not a load.
    await expect(blake.getByTestId("live-status")).toHaveAttribute("data-status", "live");
    await expect(blake.getByTestId("issue-from-r2")).toBeVisible();
    await expect(blake.getByTestId("issue-from-t31")).toBeVisible();

    await casey.getByTestId("issue-from-r2").click();
    await casey.getByTestId("issue-from-t31").click();
    // The clicks did only that: the Rock didn't expand, the To-Do wasn't ticked.
    await expect(casey.getByTestId("rock-r2")).toHaveAttribute("data-state", "closed");
    await expect(casey.getByTestId("todo-t31").locator('input[type="checkbox"]')).not.toBeChecked();

    // Blake, without reloading: badges replace the buttons, and the Issues name their sources.
    await expect(blake.getByTestId("issue-from-r2")).toHaveCount(0, { timeout: FOLLOW_MS });
    await expect(blake.getByTestId("issue-from-t31")).toHaveCount(0, { timeout: FOLLOW_MS });
    await expect(blake.getByTestId("rock-r2")).toContainText("Issue");
    await expect(blake.getByTestId("issues-open")).toContainText("from Rock Hire VP of Sales");
    await expect(blake.getByTestId("issues-open")).toContainText("from overdue To-Do Send portal beta invites");

    // An Observer sees overdue / off-track items but never the button.
    const observer = await signIn(browser, "u5");
    await expect(observer.getByTestId("todo-t12")).toContainText("Overdue");
    await expect(observer.getByTestId("rock-r4")).toContainText("Off track");
    await expect(observer.getByTestId("issue-from-t12")).toHaveCount(0);
    await expect(observer.getByTestId("issue-from-r4")).toHaveCount(0);
  } finally {
    await blake.context().close();
    await second.close();
  }
});

test("bonus: a double-click on Make it an Issue never ticks the To-Do (even with network latency)", async ({ browser, baseURL }) => {
  await freshItems(baseURL!, { todos: ["t12"] });
  const page = await signIn(browser, "u3");
  await page.route("**/api/todos/*/issue", async (r) => { await new Promise((s) => setTimeout(s, 800)); await r.continue(); });
  const button = page.getByTestId("issue-from-t12");
  await expect(button).toBeVisible();
  const box = (await button.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(120); // a human double-click: the second lands while the request is in flight
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  // Judge by the server, not the screen: wait until the Issue exists, let any in-flight toggle land, then
  // the To-Do must still be not done. (A text match on "Issue" would match the button's own label.)
  const api = await request.newContext({ baseURL: baseURL! });
  await api.post("/api/auth/login", { data: { user_id: "u3" } });
  const t12 = async () => (await (await api.get("/api/teams/team_lead/todos")).json()).items.find((t: { id: string }) => t.id === "t12");
  await expect.poll(async () => (await t12()).open_issue_id, { timeout: 5_000 }).not.toBeNull();
  await page.waitForTimeout(1_500);
  expect((await t12()).done_at).toBeNull();
  await expect(page.getByTestId("todo-t12").locator('input[type="checkbox"]')).not.toBeChecked();
  await api.dispose();
});

test("bonus: the audit trail records who changed what, and another browser sees it live", async ({ browser }) => {
  const second = await firefox.launch();
  const casey = await signIn(browser, "u3"); // Chromium
  const blake = await signIn(second, "u2"); // Firefox
  try {
    await expect(blake.getByTestId("live-status")).toHaveAttribute("data-status", "live");
    // loaded (maybe empty, e.g. right after a reset), before Casey changes anything
    await expect(blake.getByTestId("activity-card")).toHaveAttribute("data-state", "success");

    const title = `Audit me ${Date.now()}`; // unique per run: no reset needed
    await casey.getByLabel("New Issue title").fill(title);
    await casey.getByRole("button", { name: "Add", exact: true }).click();

    // Blake didn't reload: the change arrives as a hint and the feed refetches.
    await expect(blake.getByTestId("activity").locator("li").first())
      .toContainText(`Casey Cho created Issue “${title}”`, { timeout: FOLLOW_MS });
    // and the history can't be edited from the UI: the feed has no controls
    await expect(blake.getByTestId("activity").getByRole("button")).toHaveCount(0);
  } finally {
    await blake.context().close();
    await second.close();
  }
});
