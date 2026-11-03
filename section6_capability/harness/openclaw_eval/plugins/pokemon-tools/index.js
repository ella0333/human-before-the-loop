import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const SERVER = "http://127.0.0.1:8765";
const ACTIVE = "/run/pokemon-eval/active-cell.json";
const ACTIONS = [
  "press_a", "press_b", "press_start", "press_select",
  "walk_up", "walk_down", "walk_left", "walk_right",
  "hold_a_30", "wait_60", "a_until_dialog_end",
];

const NOUS_NAVIGATION_GUIDANCE = [
  "The A1..J9 grid is player-relative: you are always @ at E5; collision.player_cell remaining E5 is normal, not a desync.",
  "Use the ASCII map for WHERE to walk and the screenshot for WHAT objects, doors, signs, and NPCs are.",
  "In ASCII, . is walkable and # is blocked. Count from E5: up=row-1, down=row+1, left=col-1, right=col+1. Never route through #.",
  "Take short batches of 2-4 game inputs, then observe the updated grid again.",
].join(" ");

const schema = (properties, required = Object.keys(properties)) => ({
  type: "object", properties, required, additionalProperties: false,
});

async function activeCell() {
  return JSON.parse(await readFile(ACTIVE, "utf8"));
}

async function record(kind, detail) {
  const active = await activeCell();
  await mkdir(dirname(active.tool_log), { recursive: true });
  await appendFile(active.tool_log, JSON.stringify({
    ts: Date.now() / 1000, kind, ...detail,
  }) + "\n");
}

async function fetchOk(path, options = {}) {
  const response = await fetch(`${SERVER}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Eval-Source": "openclaw",
      ...(options.headers || {}) },
  });
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`);
  return response;
}

async function readState() {
  return (await fetchOk("/state")).json();
}

async function completionLatch(state) {
  if (!state?.flags?.has_pokedex) return false;
  const confirmation = await readState();
  if (!confirmation?.flags?.has_pokedex) return false;
  const active = await activeCell();
  await writeFile(active.completion_latch, JSON.stringify({
    ts: Date.now() / 1000, rule: "flags.has_pokedex true twice",
  }) + "\n", { mode: 0o600 });
  return true;
}

export default definePluginEntry({
  id: "pokemon-tools",
  name: "Pokemon Evaluation Tools",
  description: "Native access to the official pokemon-agent REST API.",
  register(api) {
    api.registerTool({
      name: "pokemon_observe",
      description: `Observe the current Pokemon game. Returns official structured state, the RAM-derived ASCII walkability map, and a current labelled grid screenshot as a real PNG image. Nous navigation semantics: ${NOUS_NAVIGATION_GUIDANCE}`,
      parameters: schema({}),
      async execute() {
        const [stateResponse, mapResponse, imageResponse] = await Promise.all([
          fetchOk("/state"), fetchOk("/map/ascii"),
          fetchOk("/screenshot/grid?scale=3"),
        ]);
        const state = await stateResponse.json();
        const asciiMap = await mapResponse.text();
        const image = Buffer.from(await imageResponse.arrayBuffer());
        const detail = {
          map: state?.map?.map_name || null,
          dialog_active: Boolean(state?.dialog?.active),
          in_battle: Boolean(state?.battle?.in_battle),
          image_bytes: image.length,
        };
        await record("observe", detail);
        return {
          content: [
            { type: "text", text: JSON.stringify({
              nous_navigation_guidance: NOUS_NAVIGATION_GUIDANCE,
              state, ascii_map: asciiMap,
            }) },
            { type: "image", data: image.toString("base64"), mimeType: "image/png" },
          ],
          details: detail,
        };
      },
    });

    api.registerTool({
      name: "pokemon_action",
      description: `Send one official action request to the current Pokemon game. Dialogue, movement, menus, and battle inputs all use this same tool. The response is the official pokemon-agent result and state_after. Use short batches of 2-4 inputs and remember that collision.player_cell is always the player-relative E5 center cell.`,
      parameters: schema({
        actions: {
          type: "array", minItems: 1, maxItems: 16,
          items: { type: "string", enum: ACTIONS },
          description: "One or more official pokemon-agent actions, executed in order.",
        },
      }),
      async execute(_id, params) {
        const response = await fetchOk("/action", {
          method: "POST", body: JSON.stringify({ actions: params.actions }),
        });
        const result = await response.json();
        const state = result.state_after || await readState();
        const complete = await completionLatch(state);
        const detail = {
          actions: params.actions, tutorial_complete: complete,
          map: state?.map?.map_name || null,
          dialog_active: Boolean(state?.dialog?.active),
          in_battle: Boolean(state?.battle?.in_battle),
        };
        await record("action", detail);
        return {
          content: [{ type: "text", text: JSON.stringify({
            ...result, tutorial_complete: complete,
            nous_navigation_guidance: NOUS_NAVIGATION_GUIDANCE,
          }) }],
          details: detail,
        };
      },
    });
  },
});
