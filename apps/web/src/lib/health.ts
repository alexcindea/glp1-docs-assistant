import {
  API_URL,
  createApiClient,
  type ApiClient,
  type Schemas,
} from "./api/client";

export type ReadinessReport = Schemas["ReadinessReport"];

export type HealthResult =
  | { kind: "report"; httpStatus: number; report: ReadinessReport }
  | { kind: "unreachable"; apiUrl: string; message: string };

/**
 * Ask the API whether it can serve requests. A 503 still carries a full report
 * (that is how the API says what is wrong), so it is not treated as a failure here.
 */
export async function fetchHealth(
  client: ApiClient = createApiClient(),
  apiUrl: string = API_URL,
): Promise<HealthResult> {
  try {
    const { data, error, response } = await client.GET("/health/ready", {
      cache: "no-store",
    });
    const report = data ?? error;
    if (report && "database" in report) {
      return { kind: "report", httpStatus: response.status, report };
    }
    return {
      kind: "unreachable",
      apiUrl,
      message: `The API answered with an unexpected response (HTTP ${response.status}).`,
    };
  } catch (error) {
    // fetch rejects with TypeError on network failure; anything else is a bug.
    if (!(error instanceof TypeError)) throw error;
    return {
      kind: "unreachable",
      apiUrl,
      message: `Cannot reach the API at ${apiUrl}. Start it with \`cd services/api; uv run fastapi dev\`.`,
    };
  }
}
