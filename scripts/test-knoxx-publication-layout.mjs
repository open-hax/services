#!/usr/bin/env node
// Native compiled Knoxx CMS/provenance regression. Synthetic temporary state
// only; no server, Mongo, providers, credentials, or deployed files are read.
// Build the selected Knoxx backend first, then pass its checkout directory.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, rename, rm, symlink, writeFile, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const servicesRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const checkout = process.argv[2];
if (!checkout || process.argv.length !== 3) {
  console.error('Usage: node scripts/test-knoxx-publication-layout.mjs /absolute/path/to/built-knoxx-checkout');
  process.exit(2);
}

// Read the authored definition with the same YAML dependency as Services CI.
// No rendered .env or secret-bearing deployment state is opened.
const definition = spawnSync('python3', ['-c', String.raw`
import json, pathlib, re, sys, yaml
root = pathlib.Path(sys.argv[1])
template = (root / 'digitalocean/services/knoxx/env.template').read_text()
value = re.search(r"^KNOXX_GENERATED_CONTRACTS_DIR='([^']+)'$", template, re.M).group(1)
backend = yaml.safe_load((root / 'digitalocean/services/knoxx/compose.yaml').read_text())['services']['knoxx-backend']
print(json.dumps({'generated': value, 'content': backend['environment']['KNOXX_PUBLICATION_CONTENT_ROOT'], 'volumes': backend['volumes']}))
`, servicesRoot], { encoding: 'utf8' });
assert.equal(definition.status, 0, definition.stderr || 'cannot read Services YAML');
const layout = JSON.parse(definition.stdout);
assert.equal(layout.generated, '/app/workspace/.knoxx/contracts');
assert.equal(layout.content, '/srv/website-content');
assert.ok(layout.volumes.includes('${KNOXX_WORKSPACE_PATH:?KNOXX_WORKSPACE_PATH must be set}:/app/workspace'));
assert.ok(layout.volumes.includes('${WEBSITE_CONTENT_ROOT:?WEBSITE_CONTENT_ROOT must be set}:/app/workspace/.knoxx/website-content'));
assert.ok(!layout.volumes.some((volume) => volume.endsWith(':/srv/website-content')));

const imageDefinition = await readFile(join(servicesRoot, 'digitalocean/services/knoxx/Dockerfile.backend-sdk'), 'utf8');
assert.match(imageDefinition, /RUN test ! -e \/srv\/website-content\s+\\\s+&& test ! -L \/srv\/website-content/);
assert.match(imageDefinition, /ln -s \/app\/workspace\/\.knoxx\/website-content \/srv\/website-content/);
assert.ok(imageDefinition.indexOf('ln -s /app/workspace/.knoxx/website-content') < imageDefinition.lastIndexOf('USER 1000'));

const compiled = join(resolve(checkout), 'backend/dist/cljs-runtime');
try {
  await import(pathToFileURL(join(compiled, 'knoxx.backend.infra.publication_source_revision.js')).href);
  await import(pathToFileURL(join(compiled, 'knoxx.backend.extern.publication_draft_store.js')).href);
} catch (error) {
  console.error('FAIL selected Knoxx compiled native modules unavailable; build that backend first.');
  throw error;
}
const core = globalThis.cljs.core;
const reader = globalThis.cljs.reader;
const revisions = globalThis.knoxx.backend.infra.publication_source_revision;
const cms = globalThis.knoxx.backend.infra.cms_store;
const drafts = globalThis.knoxx.backend.extern.publication_draft_store;
const edn = (text) => reader.read_string(text);
const get = (data, key) => core.get(data, core.keyword(key));
assert.equal(typeof revisions.canonical_document_path_BANG_, 'function');
assert.equal(typeof cms.save_BANG_, 'function');

const fixture = await mkdtemp(join(tmpdir(), 'knoxx-publication-layout-'));
const savedEnvironment = Object.fromEntries(['KNOXX_PUBLICATION_CONTENT_ROOT', 'KNOXX_GENERATED_CONTRACTS_DIR'].map((name) => [name, process.env[name]]));
try {
  // The old container geometry, without aliasing. The fixture uses the native
  // CMS save and immutable history rather than supplying a made-up source fact.
  const generated = join(fixture, layout.generated);
  const content = join(fixture, layout.content);
  const containedContent = join(dirname(generated), 'website-content');
  const authored = join(fixture, 'authored/contracts');
  await mkdir(generated, { recursive: true });
  await mkdir(content, { recursive: true });
  await mkdir(authored, { recursive: true });
  process.env.KNOXX_PUBLICATION_CONTENT_ROOT = content;
  process.env.KNOXX_GENERATED_CONTRACTS_DIR = generated;
  const source = '# Synthetic English CMS source\n\nImmutable source bytes for provenance proof.\n';
  const record = cms.save_BANG_('layout-probe', 'fixture-author', null,
    edn(`{:title "Layout probe" :content ${JSON.stringify(source)} :parents []}`));
  const id = get(record, 'doc_id');
  const manifestPath = join(generated, `cms-layout-probe-${id}.edn`);
  const manifestBefore = await readFile(manifestPath, 'utf8');
  const document = core.first(get(edn(manifestBefore), 'resources'));
  const sourcePath = get(get(document, 'document/source'), 'path');
  assert.ok(sourcePath.startsWith(join(content, '.ημ/cms/layout-probe/snapshots') + '/'));
  assert.equal(await readFile(sourcePath, 'utf8'), source);
  const acceptedHistoryBytes = async (contentRoot) => {
    const directory = join(contentRoot, '.ημ/cms/layout-probe/ledgers');
    const names = (await readdir(directory)).filter((name) => !name.startsWith('.') && name.endsWith('.edn')).sort();
    assert.ok(names.length > 0, 'native CMS save must create accepted history');
    return Promise.all(names.map(async (name) => [name, (await readFile(join(directory, name))).toString('hex')]));
  };
  const historyBefore = await acceptedHistoryBytes(content);
  const config = edn(`{:contracts-dir ${JSON.stringify(authored)} :generated-contracts-dir ${JSON.stringify(generated)}}`);
  const owner = revisions.resource_source_root(config, manifestPath);
  assert.equal(owner, dirname(generated));
  try {
    await revisions.canonical_document_path_BANG_(owner, document);
    assert.fail('old deployment geometry incorrectly admitted the CMS source');
  } catch (error) {
    assert.equal(get(core.ex_data(error), 'code'), 'document_source_outside_provenance_root');
    console.log('RED old layout: native CMS source refused with document_source_outside_provenance_root');
  }

  // Preserve pre-existing generated paths, including historical absolute paths.
  const draftPaths = drafts.draft_paths(generated, 'legacy-draft', 'drafts/legacy-draft.md');
  const draftPath = get(draftPaths, 'content-path');
  const completionPath = get(draftPaths, 'completion-path');
  await mkdir(dirname(draftPath), { recursive: true });
  await mkdir(dirname(completionPath), { recursive: true });
  await writeFile(draftPath, 'Existing generated draft bytes.\n');
  await writeFile(completionPath, '{:fixture/completed true}\n');
  const draftBefore = await readFile(draftPath, 'utf8');
  const completionBefore = await readFile(completionPath, 'utf8');

  // Simulate mounting the same synthetic content at the contained destination
  // and the image alias. This rename is fixture setup, never a host migration.
  await rename(content, containedContent);
  await symlink(containedContent, content);
  // Resource loading hydrates the current CMS source from native history.
  // Clio may canonicalize the changed alias, so a pre-reload Document is stale;
  // refresh through the actual projection instead of bypassing that guard.
  const recordResource = core.assoc(edn('{:ok? true :resource/kind :document}'),
    core.keyword('resource/definition'), document,
    core.keyword('resource/file-path'), manifestPath);
  const refreshed = get(cms.project_resource(recordResource), 'resource/definition');
  const canonical = await revisions.canonical_document_path_BANG_(owner, refreshed);
  assert.equal(canonical, await realpath(sourcePath));
  assert.ok(canonical.startsWith(owner + '/website-content/'));
  assert.equal(await readFile(canonical, 'utf8'), source);
  assert.equal(await realpath(get(cms.read_BANG_('layout-probe', id), 'source_path')), await realpath(sourcePath));
  assert.equal(get(cms.read_BANG_('layout-probe', id), 'revision'), get(record, 'revision'));
  assert.deepEqual(await acceptedHistoryBytes(content), historyBefore);
  assert.equal(await readFile(manifestPath, 'utf8'), manifestBefore);
  const facts = await revisions.source_revisions_BANG_(config, core.vector(refreshed),
    core.assoc(core.PersistentArrayMap.EMPTY, get(refreshed, 'document/id'), owner));
  assert.equal(core.get(facts, get(document, 'document/id')), revisions.content_revision(source));
  const historicCmsPath = edn(`{:document/id :fixture/historic-cms-path :document/title "Historic CMS path" :document/source-locale :en :document/source {:path ${JSON.stringify(sourcePath)}}}`);
  assert.equal(await revisions.canonical_document_path_BANG_(owner, historicCmsPath), canonical);
  console.log('GREEN alias layout: refreshed native CMS resolves the exact saved content revision; historic absolute source path remains contained, accepted history and manifest bytes unchanged');

  for (const [label, path] of [['relative', 'drafts/legacy-draft.md'], ['absolute', draftPath]]) {
    const draft = edn(`{:document/id :fixture/legacy-draft :document/title "Legacy draft" :document/source-locale :en :document/source {:path ${JSON.stringify(path)}}}`);
    assert.equal(await revisions.canonical_document_path_BANG_(owner, draft), await realpath(draftPath));
    console.log(`GREEN existing ${label} generated draft source remains admissible`);
  }
  const pathsAfter = drafts.draft_paths(generated, 'legacy-draft', 'drafts/legacy-draft.md');
  assert.equal(get(pathsAfter, 'content-path'), draftPath);
  assert.equal(get(pathsAfter, 'completion-path'), completionPath);
  assert.equal(await readFile(draftPath, 'utf8'), draftBefore);
  assert.equal(await readFile(completionPath, 'utf8'), completionBefore);
  console.log('GREEN generated draft/completion paths and bytes remain unchanged');

  // The layout repair must not broaden the underlying provenance guard.
  const outside = join(fixture, 'outside.md');
  await writeFile(outside, 'Synthetic unrelated bytes.\n');
  const escape = join(owner, 'escape.md');
  await symlink(outside, escape);
  const escapedDocument = edn(`{:document/id :fixture/escape :document/title "Escape" :document/source-locale :en :document/source {:path ${JSON.stringify(escape)}}}`);
  await assert.rejects(revisions.canonical_document_path_BANG_(owner, escapedDocument),
    (error) => get(core.ex_data(error), 'code') === 'document_source_outside_provenance_root');
  console.log('GREEN unrelated source symlink still refused by the native realpath guard');
} finally {
  for (const [name, value] of Object.entries(savedEnvironment)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  await rm(fixture, { recursive: true, force: true });
}
