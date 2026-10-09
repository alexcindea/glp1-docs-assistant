import { proxyToApi } from "@/lib/api/proxy";

/**
 * The browser's way to the API: `/api/<path>` is forwarded to `API_URL/<path>`,
 * streaming both ways (uploads in, answer events out). The API itself stays
 * unexposed and needs no CORS.
 */
async function handle(
  request: Request,
  context: RouteContext<"/api/[...path]">,
) {
  const { path } = await context.params;
  return proxyToApi(request, path);
}

export { handle as GET, handle as POST };
