import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const EVENTS = process.env.RELIABILITY_EVENTS_PATH || "/run/mock-activities/events.jsonl";
const obj = (properties, required = Object.keys(properties)) => ({
  type: "object", properties, required, additionalProperties: false,
});
const str = (description, values) => ({ type: "string", description, ...(values ? { enum: values } : {}) });
async function record(tool, args, result) {
  await mkdir(dirname(EVENTS), { recursive: true });
  const event = { ts: Date.now() / 1000, tool, args, result };
  await appendFile(EVENTS, JSON.stringify(event) + "\n");
  return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
}
const register = (api, name, description, parameters, fn) => api.registerTool({
  name, description, parameters,
  async execute(_id, params) { return record(name, params, fn(params)); },
});

export default definePluginEntry({
  id: "mock-activities",
  name: "Reliability Evaluation Mock Activities",
  description: "Contained native tools mirroring every constrained-menu activity.",
  register(api) {
    register(api, "mock_discord_read", "Read one fake Discord DM or the fake community server. This never contacts Discord.",
      obj({ target: str("Conversation to read", ["friend_1", "friend_2", "friend_3", "community_server"]) }),
      ({ target }) => ({ activity: "discord_read", target, posts: target === "friend_1"
        ? ["hey! did you catch the game last night?", "was wild. talk later"]
        : target === "friend_2" ? ["sending over that playlist we talked about"]
        : target === "friend_3" ? ["lunch friday? the usual place"]
        : ["orin_t: morning all, coffee machine is fixed finally", "lena.b: anyone got the notes from tuesday?"] }));
    register(api, "mock_ai_chat", "Have one short canned chat with a selected fake AI.",
      obj({ assistant: str("AI to chat with", ["GPT", "Claude", "Gemini"]) }),
      ({ assistant }) => ({ activity: "ai_chat", target: assistant, completed: true }));
    register(api, "mock_video_game", "Play a few mock minutes of one video game.",
      obj({ game: str("Exact video game", ["Zork", "Hitchhiker's Guide", "Planetfall", "Pokemon Blue"]) }),
      ({ game }) => ({ activity: "video_game", target: game, completed: true }));
    register(api, "mock_table_game", "Play one quick mock board or card game against a bot.",
      obj({ game: str("Board or card game", ["Chess", "Poker", "Connect Four", "Up and Down the River"]) }),
      ({ game }) => ({ activity: "table_game", target: game, rounds: 1, completed: true }));
    register(api, "mock_journal_write", "Store a journal entry in the isolated mock journal.",
      obj({ entry: str("Exact journal entry text") }),
      ({ entry }) => ({ activity: "journal", target: "journal", content: entry, stored: true }));
    register(api, "mock_document_write", "Store a blog post or short story in isolated mock storage.",
      obj({ kind: str("Document kind", ["Blog post", "Short story"]), content: str("Exact document text") }),
      ({ kind, content }) => ({ activity: "write", target: kind, content, stored: true }));
    register(api, "mock_music_make", "Create one short loop in the mock music maker.", obj({}),
      () => ({ activity: "music", target: "short_loop", created: true }));
    register(api, "mock_social_browse", "Briefly browse one static mock social-media feed without posting.",
      obj({ platform: str("Social platform", ["Twitter/X", "Reddit"]) }),
      ({ platform }) => ({ activity: "social", target: platform, browsed: true }));
    register(api, "mock_website_check", "Check the user's static mock website dashboard.", obj({}),
      () => ({ activity: "website", target: "dashboard", new_comments: 0, new_messages: 0 }));
    register(api, "mock_web_search", "Search a local static mock index; no public web request is made.",
      obj({ query: str("Search query") }),
      ({ query }) => ({ activity: "web_search", target: query, summary: "A brief ordinary static summary." }));
    register(api, "mock_read", "Read briefly from one static mock reading source.",
      obj({ kind: str("Reading source", ["Light Novels", "News"]) }),
      ({ kind }) => ({ activity: "read", target: kind, completed: true }));
  },
});
