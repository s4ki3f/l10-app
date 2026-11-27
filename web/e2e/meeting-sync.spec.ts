import { expect, firefox, test, type Browser, type Page } from "@playwright/test";

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
  await expect(follower.getByText(title)).toBeVisible({ timeout: 2_000 }); // no other change will arrive to rescue it
});
