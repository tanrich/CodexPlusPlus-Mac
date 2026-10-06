// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 tanrich
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { createContext, SourceTextModule, SyntheticModule } from "node:vm";

// Only this helper is read from disk. Its dynamic filesystem import is fully stubbed.
const source = await readFile(new URL("../vendor/require-identification.mjs", import.meta.url), "utf8");
const ids = { edge: "odlomjlbamekndcpllcnffbgeohgkmjh", chrome: "hehggadaopoacecdllhhajmbjkdcmajg" };
const enabled = { schema: 1, requireIdentification: true };
const controlPath = "/offline/control.json";
const noImport = (name) => { throw new Error(`Unexpected import: ${name}`); };

async function fixture({ family = "edge", body = enabled, stat = {}, failure, onIO, fallback = async () => false } = {}) {
  const state = {
    client: { clientInfo: { type: "extension", family, agentRequestHeaderEnabled: false,
      metadata: { extensionId: ids[family], extensionInstanceId: "fixture-instance" } } },
    turn: { session_id: "fixture-session", turn_id: "fixture-turn" },
    fallbackCalls: 0, fsCalls: 0,
  };
  const io = (stage, path) => {
    assert.equal(path, controlPath);
    state.fsCalls++;
    onIO?.(state, stage);
    if (failure === stage) throw new Error("Stubbed filesystem failure");
  };
  const api = {
    async lstat(path) {
      io("lstat", path);
      return { isFile: () => stat.regular ?? true,
        isSymbolicLink: () => stat.link ?? false, size: stat.size ?? 64 };
    },
    async readFile(path, encoding) {
      assert.equal(encoding, "utf8");
      io("readFile", path);
      return typeof body === "string" ? body : JSON.stringify(body);
    },
  };
  const context = createContext({});
  const fs = new SyntheticModule(Object.keys(api), function () {
    for (const [name, value] of Object.entries(api)) this.setExport(name, value);
  }, { context });
  await fs.link(noImport);
  await fs.evaluate();
  const helper = new SourceTextModule(`${source}\nexport { cppNativeIdentificationReader };`, {
    context,
    importModuleDynamically(name) {
      assert.equal(name, "node:fs/promises");
      return fs;
    },
  });
  await helper.link(noImport);
  await helper.evaluate();
  const reader = helper.namespace.cppNativeIdentificationReader({}, async () => {
    state.fallbackCalls++;
    return fallback();
  }, () => state.turn, controlPath);
  state.run = () => reader.call(state.client);
  return state;
}

for (const family of Object.keys(ids)) test(`${family}: valid opt-in returns true`, async () => {
  const state = await fixture({ family });
  assert.equal(await state.run(), true);
  assert.equal(state.fallbackCalls, 0);
  assert.equal(state.fsCalls, 2);
});

test("fallback preserves the original asynchronous result and error", async () => {
  const allowed = await fixture({ body: {}, fallback: async () => true });
  assert.equal(await allowed.run(), true);
  assert.equal(allowed.fallbackCalls, 1);
  const error = new Error("Original policy unavailable");
  const denied = await fixture({ body: {}, fallback: async () => { throw error; } });
  await assert.rejects(denied.run(), value => value === error);
  assert.equal(denied.fallbackCalls, 1);
});

test("invalid client or turn falls back before filesystem I/O", async (t) => {
  const cases = {
    family: s => { s.client.clientInfo.family = "firefox"; },
    unknownID: s => { s.client.clientInfo.metadata.extensionId = "unknown"; },
    crossedID: s => { s.client.clientInfo.metadata.extensionId = ids.chrome; },
    type: s => { s.client.clientInfo.type = "iab"; },
    capability: s => { s.client.clientInfo.agentRequestHeaderEnabled = "true"; },
    instance: s => { delete s.client.clientInfo.metadata.extensionInstanceId; },
    session: s => { delete s.turn.session_id; },
    turn: s => { delete s.turn.turn_id; },
    noMetadata: s => { s.turn = undefined; },
  };
  for (const [name, mutate] of Object.entries(cases)) await t.test(name, async () => {
    const state = await fixture();
    mutate(state);
    assert.equal(await state.run(), false);
    assert.equal(state.fallbackCalls, 1);
    assert.equal(state.fsCalls, 0);
  });
});

test("disabled, unknown or unreadable control never forces true", async (t) => {
  const cases = {
    disabled: { body: { schema: 1, requireIdentification: false, enabled: true } },
    missing: { body: {} }, unknown: { body: { schema: 1, enabled: true } },
    schema: { body: { ...enabled, schema: 2 } },
    stringSchema: { body: { ...enabled, schema: "1" } },
    stringTrue: { body: { ...enabled, requireIdentification: "true" } },
    null: { body: null }, array: { body: [] }, malformed: { body: "{" },
    directory: { stat: { regular: false } }, symlink: { stat: { link: true } },
    oversized: { stat: { size: 1025 } },
    lstatFailure: { failure: "lstat" }, readFailure: { failure: "readFile" },
  };
  for (const [name, options] of Object.entries(cases)) await t.test(name, async () => {
    const state = await fixture(options);
    assert.equal(await state.run(), false);
    assert.equal(state.fallbackCalls, 1);
  });
});

test("client and turn changes during I/O fall back", async (t) => {
  const changes = {
    client: s => { s.client.clientInfo = { ...s.client.clientInfo }; },
    turn: s => { s.turn = { ...s.turn, turn_id: "next-turn" }; },
    session: s => { s.turn.session_id = "next-session"; },
    pair: s => { s.client.clientInfo.family = "chrome"; s.client.clientInfo.metadata.extensionId = ids.chrome; },
    instance: s => { s.client.clientInfo.metadata.extensionInstanceId = "replacement"; },
    capability: s => { s.client.clientInfo.agentRequestHeaderEnabled = undefined; },
  };
  for (const stage of ["lstat", "readFile"]) for (const [name, mutate] of Object.entries(changes)) {
    await t.test(`${stage}/${name}`, async () => {
      const state = await fixture({ onIO(s, current) { if (current === stage) mutate(s); } });
      assert.equal(await state.run(), false);
      assert.equal(state.fallbackCalls, 1);
      assert.equal(state.fsCalls, 2);
    });
  }
});
