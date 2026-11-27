export { cn } from "cn"

/** 3 + "%" → "3 %" (a non-breaking space: the unit never wraps alone); counts carry no unit. */
export const withUnit = (v: number | string | null | undefined, unit: string) =>
  !unit || unit === "count" ? `${v}` : `${v}\u00a0${unit}`;
