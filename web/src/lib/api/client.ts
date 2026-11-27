import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

/** Same-origin client: Next.js forwards /api/* to FastAPI, so the session cookie just works. */
export const api = createClient<paths>({ baseUrl: "/api", credentials: "same-origin" });

/** The one error shape, generated from the API contract (ErrorOut in openapi.json). */
export type ApiErrorBody = components["schemas"]["ErrorOut"];

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields: { field: string; message: string }[] = [],
    /** for 409 stale_write: what's stored now (value/priority, version, updated_by) */
    public current: Record<string, unknown> | undefined = undefined,
  ) {
    super(message);
  }
}

/** Turn an openapi-fetch result into data or a typed ApiError (one error shape from the server). */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const body = result.error as Partial<ApiErrorBody> | undefined;
    throw new ApiError(
      result.response.status,
      body?.error?.code ?? "http_error",
      body?.error?.message ?? result.response.statusText,
      body?.error?.fields ?? [],
      body?.error?.current ?? undefined,
    );
  }
  return result.data as T;
}
