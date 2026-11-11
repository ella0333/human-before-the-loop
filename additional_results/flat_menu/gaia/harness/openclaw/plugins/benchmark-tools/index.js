import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const EVENTS = process.env.BENCHMARK_EVENTS_PATH || "/run/benchmark-tools/events.jsonl";
const obj = (properties) => ({ type: "object", properties, required: Object.keys(properties), additionalProperties: false });
const str = (description) => ({ type: "string", description });

async function record(tool, args, result) {
  await mkdir(dirname(EVENTS), { recursive: true });
  await appendFile(EVENTS, JSON.stringify({ ts: Date.now() / 1000, tool, args, result }) + "\n");
  return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
}

async function tavily(endpoint, payload) {
  const key = String(process.env.TAVILY_API_KEY || "").trim();
  if (!key) return { error: "TAVILY_API_KEY is not configured", results: [] };
  try {
    const response = await fetch(`https://api.tavily.com/${endpoint}`, { method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal: AbortSignal.timeout(30000) });
    const body = await response.json();
    if (!response.ok) return { error: `Tavily HTTP ${response.status}`, results: [] };
    return body;
  } catch (error) { return { error: `retrieval failed: ${error.constructor.name}`, results: [] }; }
}

async function search(query) {
  query = String(query || "").trim().slice(0, 500);
  if (!query) return { error: "empty search query", results: [] };
  const body = await tavily("search", { query, search_depth: "advanced", max_results: 5,
    include_answer: false, include_raw_content: false });
  return { query, error: body.error,
    results: (body.results || []).slice(0, 5).map((row) => ({
      title: String(row.title || "").slice(0, 200), url: String(row.url || "").slice(0, 1000),
      content: String(row.content || "").slice(0, 3000) })) };
}

async function browse(url) {
  url = String(url || "").trim().slice(0, 1000);
  if (!/^https?:\/\//i.test(url)) return { error: "only public HTTP(S) page URLs are accepted", results: [] };
  const body = await tavily("extract", { urls: [url], extract_depth: "basic",
    include_images: false, format: "markdown" });
  return { url, error: body.error, results: (body.results || []).slice(0, 1).map((row) => ({
    url: String(row.url || url).slice(0, 1000), content: String(row.raw_content || "").slice(0, 12000) })) };
}

function calculator(source) {
  const raw = String(source || "").slice(0, 500);
  const tokens = raw.match(/\d+(?:\.\d+)?(?:e[+-]?\d+)?|\*\*|[()+\-*/%]/gi) || [];
  if (tokens.join("") !== raw.replace(/\s/g, "")) throw new Error("unsupported calculator syntax");
  let at = 0;
  const primary = () => { if (tokens[at] === "(") { at++; const v = add(); if (tokens[at++] !== ")") throw new Error("missing )"); return v; } const v = Number(tokens[at++]); if (!Number.isFinite(v)) throw new Error("expected number"); return v; };
  const power = () => { let v = primary(); if (tokens[at] === "**") { at++; const e = unary(); if (Math.abs(e) > 100) throw new Error("exponent too large"); v **= e; } return v; };
  const unary = () => tokens[at] === "+" ? (at++, unary()) : tokens[at] === "-" ? (at++, -unary()) : power();
  const mul = () => { let v = unary(); while (["*", "/", "%"].includes(tokens[at])) { const op = tokens[at++], r = unary(); v = op === "*" ? v * r : op === "/" ? v / r : v % r; } return v; };
  const add = () => { let v = mul(); while (["+", "-"].includes(tokens[at])) { const op = tokens[at++], r = mul(); v = op === "+" ? v + r : v - r; } return v; };
  const value = add(); if (at !== tokens.length || !Number.isFinite(value)) throw new Error("invalid result");
  return Number(value.toPrecision(15)).toString();
}

export default definePluginEntry({ id: "benchmark-tools", name: "Contained Public-Web and Calculator Tools",
  description: "Bounded public-web retrieval through Tavily and pure arithmetic.", register(api) {
    api.registerTool({ name: "benchmark_web_search", description: "Search the public web with one bounded query.",
      parameters: obj({ query: str("Search query") }), async execute(_id, p) { return record("benchmark_web_search", p, await search(p.query)); } });
    api.registerTool({ name: "benchmark_web_browse", description: "Read one public HTTP(S) page through bounded Tavily extraction.",
      parameters: obj({ url: str("Public page URL from search results") }), async execute(_id, p) { return record("benchmark_web_browse", p, await browse(p.url)); } });
    api.registerTool({ name: "benchmark_calculate", description: "Evaluate pure arithmetic only.",
      parameters: obj({ expression: str("Pure arithmetic expression") }), async execute(_id, p) {
        let result; try { result = { expression: String(p.expression || "").trim().slice(0, 500), result: calculator(p.expression) }; }
        catch (error) { result = { expression: String(p.expression || "").trim().slice(0, 500), error: `calculation failed: ${error.message}` }; }
        return record("benchmark_calculate", p, result); } });
  }});
