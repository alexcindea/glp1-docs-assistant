import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

/** Where server components reach the API. Server-side only; never sent to the browser. */
export const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

export type ApiClient = ReturnType<typeof createClient<paths>>;
export type Schemas = components["schemas"];

export function createApiClient(
  baseUrl: string = API_URL,
  fetchImpl?: typeof fetch,
): ApiClient {
  return createClient<paths>({ baseUrl, fetch: fetchImpl });
}
