import http from "node:http";
import { appendFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { createChatChannelPlugin, defineChannelPluginEntry } from "openclaw/plugin-sdk/channel-core";
import { defineChannelMessageAdapter, createMessageReceiptFromOutboundResults } from "openclaw/plugin-sdk/channel-outbound";

const ID = "mock-discord";
const DEFAULT_PORT = 18890;
const DEFAULT_OUTBOX = "/run/mock-discord/outbox.jsonl";
const rootConfig = (cfg) => cfg.channels?.[ID] || {};
const accountIds = (cfg) => Object.keys(rootConfig(cfg).accounts || {}).length
  ? Object.keys(rootConfig(cfg).accounts) : ["default"];
const scopedConfig = (cfg, accountId = "default") =>
  rootConfig(cfg).accounts?.[accountId] || rootConfig(cfg);
const resolveAccount = (cfg, accountId = "default") => {
  const config = scopedConfig(cfg, accountId);
  return { accountId, enabled: config.enabled !== false, configured: true, config };
};
const outboxPath = (cfg, accountId) =>
  scopedConfig(cfg, accountId).outboxPath || DEFAULT_OUTBOX;
const appendOutbound = async (cfg, accountId, row) =>
  appendFile(outboxPath(cfg, accountId), JSON.stringify(row) + "\n");

const message = defineChannelMessageAdapter({
  id: ID,
  durableFinal: { capabilities: { text: true, media: false, replyTo: false, thread: false, messageSendingHooks: false } },
  send: { text: async (ctx) => {
    const messageId = `mock-${randomUUID()}`;
    await appendOutbound(ctx.cfg, ctx.accountId, {
      id: messageId, sender: "openclaw", accountId: ctx.accountId, target: ctx.to, text: ctx.text,
    });
    return { messageId, receipt: createMessageReceiptFromOutboundResults({
      results: [{ channel: ID, messageId }], kind: "text",
    }) };
  } },
});

async function dispatch(ctx, event) {
  const runtime = ctx.channelRuntime;
  if (!runtime) throw new Error("mock-discord: channel runtime unavailable");
  const conversationId = event.conversationId || "community-server";
  const route = runtime.routing.resolveAgentRoute({
    cfg: ctx.cfg, channel: ID, accountId: ctx.accountId,
    peer: { kind: "channel", id: conversationId },
  });
  const timestamp = Date.now();
  await runtime.inbound.run({
    channel: ID, accountId: ctx.accountId, raw: event,
    adapter: {
      ingest: () => ({
        id: event.id || randomUUID(), timestamp, rawText: event.text,
        textForAgent: event.text, textForCommands: event.text,
      }),
      resolveTurn: async (input) => {
        const payload = runtime.inbound.buildContext({
          channelIngress: "unsupported", channel: ID, accountId: ctx.accountId,
          messageId: input.id, timestamp, from: `channel:${conversationId}`,
          sender: { id: event.sender || "member", name: event.sender || "member" },
          conversation: {
            kind: "group", id: conversationId, label: "Community Server #general",
            nativeChannelId: conversationId,
          },
          route: {
            agentId: route.agentId, accountId: ctx.accountId,
            routeSessionKey: route.sessionKey, dispatchSessionKey: route.sessionKey,
          },
          reply: { to: `channel:${conversationId}`, originatingTo: `channel:${conversationId}` },
          message: {
            rawBody: input.rawText, commandBody: input.textForCommands,
            bodyForAgent: input.textForAgent,
          },
        });
        return {
          cfg: ctx.cfg, channel: ID, accountId: ctx.accountId,
          agentId: route.agentId,
          routeSessionKey: route.sessionKey,
          storePath: runtime.session.resolveStorePath(
            ctx.cfg.session?.store, { agentId: route.agentId }),
          ctxPayload: payload,
          recordInboundSession: runtime.session.recordInboundSession,
          dispatchReplyWithBufferedBlockDispatcher:
            runtime.reply.dispatchReplyWithBufferedBlockDispatcher,
          delivery: { deliver: async (reply) => {
            const text = reply?.text || "";
            if (text) await appendOutbound(ctx.cfg, ctx.accountId, {
              sender: "openclaw", accountId: ctx.accountId, text,
            });
            return { visibleReplySent: Boolean(text) };
          } },
          record: { onRecordError: (error) => {
            ctx.log?.error?.(`mock-discord session record failed: ${String(error)}`);
          } },
        };
      },
    },
  });
  return { sessionKey: route.sessionKey, agentId: route.agentId,
    accountId: ctx.accountId, conversationId };
}

async function startAccount(ctx) {
  const port = ctx.account?.config?.port || DEFAULT_PORT;
  const server = http.createServer(async (req, res) => {
    if (req.method !== "POST" || req.url !== "/inbound") {
      res.writeHead(404); return res.end();
    }
    let body = "";
    for await (const chunk of req) body += chunk;
    try {
      const result = await dispatch(ctx, JSON.parse(body));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(result));
    } catch (error) {
      res.writeHead(500); res.end(String(error));
    }
  });
  await new Promise((resolve, reject) => {
    const stop = () => server.close(() => resolve());
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => {
      if (ctx.abortSignal?.aborted) stop();
      else ctx.abortSignal?.addEventListener("abort", stop, { once: true });
    });
  });
}

const plugin = createChatChannelPlugin({
  base: {
    id: ID,
    meta: { id: ID, label: "Evaluation mock Discord", selectionLabel: "Evaluation mock Discord",
      docsPath: "/channels/mock-discord", blurb: "Loopback-only Discord-like evaluation channel." },
    capabilities: { chatTypes: ["group"] },
    reload: { configPrefixes: ["channels.mock-discord"] },
    config: {
      listAccountIds: (cfg) => accountIds(cfg),
      resolveAccount: (cfg, accountId) => resolveAccount(cfg, accountId),
      inspectAccount: (cfg, accountId) => resolveAccount(cfg, accountId),
      defaultAccountId: (cfg) => rootConfig(cfg).defaultAccount || accountIds(cfg)[0],
    },
    messaging: { targetPrefixes: [ID], normalizeTarget: (raw) => raw,
      inferTargetChatType: () => "group",
      targetResolver: { looksLikeId: () => true, hint: "channel:community-server" } },
    message,
    gateway: { startAccount },
  },
});

export default defineChannelPluginEntry({
  id: ID,
  name: "Evaluation mock Discord",
  description: "Loopback-only Discord-like channel for injection evaluation.",
  plugin,
});
