#!/usr/bin/env node
/**
 * Emits the source-level web API inventory to stdout. Run from repository root:
 *   node docs/performance/measurements/web-api-inventory-extract-2026-11-02.mjs > docs/performance/web-api-inventory-2026-11-02.json
 *
 * This intentionally inventories declarations, not observed browser traffic.
 */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import ts from '../../../frontend/node_modules/typescript/lib/typescript.js';

const apiFile = 'frontend/src/api.ts';
const source = readFileSync(apiFile, 'utf8');
const file = ts.createSourceFile(apiFile, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const callees = new Set(['requestJson', 'post', 'put', 'patch', 'del']);
const line = (node) => file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
const exported = (node) => (ts.getModifiers(node) || []).some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword);
const revision = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();

function collect(node, result = []) {
  if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && callees.has(node.expression.text)) result.push(node);
  ts.forEachChild(node, (child) => { collect(child, result); });
  return result;
}

function method(call) {
  const callee = call.expression.text;
  if (callee === 'post') return 'POST';
  if (callee === 'put') return 'PUT';
  if (callee === 'patch') return 'PATCH';
  if (callee === 'del') return 'DELETE';
  const init = call.arguments[1];
  if (init && ts.isObjectLiteralExpression(init)) {
    const property = init.properties.find((candidate) => ts.isPropertyAssignment(candidate) && candidate.name.getText(file) === 'method');
    if (property && ts.isPropertyAssignment(property) && ts.isStringLiteral(property.initializer)) return property.initializer.text;
  }
  return 'GET';
}

function classification(name, path) {
  if (['post', 'put', 'del'].includes(name)) return 'transport_helper';
  if (['IMPORTS', 'WATCH_QUEUE_URL', 'MODELS', 'AUTOMATION'].includes(path)) return 'symbolic_static_path';
  if (path.startsWith('person(') || path.startsWith('slot(')) return 'path_builder';
  return 'network_wrapper';
}

const wrappers = [];
for (const statement of file.statements) {
  if (!exported(statement)) continue;
  const declarations = ts.isVariableStatement(statement)
    ? statement.declarationList.declarations.filter((declaration) => ts.isIdentifier(declaration.name) && declaration.initializer).map((declaration) => ({ name: declaration.name.text, body: declaration.initializer }))
    : ts.isFunctionDeclaration(statement) && statement.name ? [{ name: statement.name.text, body: statement }] : [];
  for (const declaration of declarations) {
    if (declaration.name === 'requestJson') {
      // Its recursive CSRF retry is not a separate endpoint. The function owns
      // the actual dynamic-method `fetch` at api.ts:189.
      wrappers.push({ name: 'requestJson', method: 'dynamic (init.method; GET default)', path_expression: 'path', source_file: apiFile, source_line: 189, classification: 'transport_helper', call_index: 1 });
      continue;
    }
    collect(declaration.body).forEach((call, index) => {
      const path = call.arguments[0] ? call.arguments[0].getText(file) : null;
      wrappers.push({ name: declaration.name, method: method(call), path_expression: path, source_file: apiFile, source_line: line(call), classification: classification(declaration.name, path || ''), call_index: index + 1 });
    });
  }
}

const direct = [
  ['requestJson transport fetch', 'fetch', 'dynamic (GET by default)', 'apiBaseUrl + path', apiFile, 189, 'shared_transport'],
  ['downloadBackup', 'fetch', 'POST', '/api/admin/backups/{name}/download', apiFile, 768, 'direct_blob_download'],
  ['connectEvents', 'EventSource', 'GET', '/api/events', apiFile, 825, 'server_sent_events'],
  ['beaconRecoEvents', 'navigator.sendBeacon', 'POST', '/api/reco/events', apiFile, 498, 'pagehide_beacon'],
  ['beaconClientMetrics', 'navigator.sendBeacon', 'POST', '/api/metrics/client', apiFile, 1004, 'pagehide_beacon'],
  ['getBookPageBytes', 'fetch', 'GET', 'bookPageUrl(id, index)', 'frontend/src/features/books/booksApi.ts', 22, 'binary_book_page'],
  ['defaultGetAudio', 'fetch', 'GET', 'source.url', 'frontend/src/features/music/musicEngine.ts', 263, 'audio_buffer_fallback'],
  ['boundedResponse', 'fetch', 'GET', 'url', 'frontend/src/features/music/musicOffline.ts', 174, 'offline_audio_download'],
  ['musicSession read music', 'fetch', 'GET', 'source.url', 'frontend/src/features/music/musicSession.ts', 351, 'audio_buffer_fallback'],
  ['defaultProbe', 'fetch', 'HEAD', 'url', 'frontend/src/features/music/musicStream.ts', 38, 'audio_access_probe'],
  ['openStream', 'fetch', 'POST', '/api/admin/household-transfer/{provider}/import', 'frontend/src/features/onboarding/onboardingTransferApi.ts', 50, 'finite_sse_stream'],
  ['uploadPhoto', 'XMLHttpRequest', 'PUT', '/api/photos/import?filename={encoded filename}', 'frontend/src/features/photos/photoApi.ts', 156, 'upload_with_progress'],
  ['LocalPlayer media-error probe', 'fetch', 'GET', 'libraryMediaUrl(playId)', 'frontend/src/localPlayer.tsx', 439, 'range_diagnostic_probe'],
].map(([name, primitive, methodName, pathExpression, sourceFile, sourceLine, kind]) => ({ name, primitive, method: methodName, path_expression: pathExpression, source_file: sourceFile, source_line: sourceLine, classification: kind }));

const urlBuilders = [
  ['libraryMediaUrl', 'GET (browser media element)', '/api/library/{id}/media', apiFile, 562],
  ['bookPageUrl', 'GET (used by getBookPageBytes)', '/api/books/{id}/pages/{index}', 'frontend/src/features/books/booksApi.ts', 19],
  ['bookResourceUrl', 'GET (browser resource loader)', '/api/books/{id}/resources?member={member}', 'frontend/src/features/books/booksApi.ts', 29],
].map(([name, methodName, pathExpression, sourceFile, sourceLine]) => ({ name, method: methodName, path_expression: pathExpression, source_file: sourceFile, source_line: sourceLine, classification: 'url_builder_not_a_fetch' }));

const classCounts = wrappers.reduce((result, entry) => ({ ...result, [entry.classification]: (result[entry.classification] || 0) + 1 }), {});
const reexports = file.statements.filter(ts.isExportDeclaration).map((node) => ({ source_file: apiFile, source_line: line(node), text: node.getText(file) }));
const inventory = {
  schema_version: 1,
  source_revision: revision,
  generated_from: { source: apiFile, parser: 'TypeScript compiler API already installed in frontend/node_modules', extraction: 'AST direct-call traversal; manual list for non-api.ts primitives' },
  scope: { included: ['every exported api.ts declaration that directly calls requestJson/post/put/patch/del', 'direct browser network primitives in production frontend source', 'explicit URL builders that identify browser-controlled API resources'], excluded: ['type-only exports', 'constants and non-network helpers', 'test files', 'browser image/video/audio subrequests whose URLs originate from response data', 'native clients and backend-to-backend traffic'] },
  counts: { api_ts_wrapper_call_sites: wrappers.length, api_ts_wrapper_classifications: classCounts, direct_network_sites: direct.length, url_builders: urlBuilders.length, reexports: reexports.length },
  api_ts_network_wrappers: wrappers,
  api_ts_url_builders: urlBuilders,
  direct_network_sites: direct,
  helpers: [
    { name: 'requestJson', source_file: apiFile, source_line: 176, classification: 'shared_transport_function', note: 'The exported helper is represented at its actual fetch call, api.ts:189; its recursive CSRF retry is not a second endpoint declaration.' },
    { name: 'patch', source_file: apiFile, source_line: 258, classification: 'non_exported_transport_helper', note: 'Included only indirectly through exported callers that invoke it.' },
  ],
  reexports,
  unresolved_dynamic_paths: [
    { name: 'requestJson', source_file: apiFile, source_line: 176, path_expression: 'path', reason: 'shared transport accepts caller-supplied relative path and init.method' },
    { name: 'post', source_file: apiFile, source_line: 256, path_expression: 'path', reason: 'shared mutation helper accepts caller-supplied relative path' },
    { name: 'put', source_file: apiFile, source_line: 257, path_expression: 'path', reason: 'shared mutation helper accepts caller-supplied relative path' },
    { name: 'del', source_file: apiFile, source_line: 259, path_expression: 'path', reason: 'shared mutation helper accepts caller-supplied relative path' },
    { name: 'music direct fetches', source_file: 'frontend/src/features/music/{musicEngine,musicOffline,musicSession,musicStream}.ts', source_line: null, path_expression: 'source.url or url', reason: 'runtime URL originates from the application playback source; static endpoint cannot be inferred' },
  ],
  limitations: [
    'One exported wrapper with conditional branches contributes one entry per direct transport call; call_index preserves that distinction.',
    'Symbolic constants and path builders are retained rather than textually evaluated. They are statically resolvable in source but recorded as expressions to preserve provenance.',
    'The inventory does not enumerate every browser-controlled img, video, audio, CSS, HLS segment, DASH segment, or signed artwork request. Those require a browser/network census and response-header capture.',
    'No production traffic, latency, byte size, retry outcome, cache outcome, or server work was measured.',
  ],
};
process.stdout.write(`${JSON.stringify(inventory, null, 2)}\n`);
