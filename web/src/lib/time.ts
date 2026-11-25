/** Display helpers. All business dates come from the server; this only formats them in company time. */
const TZ = "America/Chicago";

const dayFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const momentFmt = new Intl.DateTimeFormat("en-US", {
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: TZ,
});

/** "2026-11-02" → "Nov 2" (plain calendar dates, no time zone shift). */
export const formatDay = (isoDate: string) => dayFmt.format(new Date(`${isoDate}T00:00:00Z`));

/** UTC timestamp → "Nov 4, 9:40 AM" in Chicago. */
export const formatMoment = (iso: string) => momentFmt.format(new Date(iso));

/** 75 → "1:15", 3725 → "1:02:05". */
export function formatDuration(totalSeconds: number) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}
