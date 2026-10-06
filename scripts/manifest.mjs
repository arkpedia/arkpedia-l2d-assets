// Validation of manifest.json and every models/<slug>/<md5_12>/ folder.
// Used by scripts/validate.mjs (CI and the sync workflow) and the tests.
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { inspectSkeleton, isJsonSkeleton, readAtlas } from './spine.mjs';

export const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

const SKIN_ID = /^[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?#[0-9]+$/;
const HEX64 = /^[a-f0-9]{64}$/;
const MD5 = /^[a-f0-9]{32}$/;
const VERSION = /^\d+\.\d+\.\d+$/;
const ENTRANCE_ID = /^dyn_entrance_[A-Za-z0-9_#]+$/;

/** skinId with '@' and '#' replaced by '_'. Mirrors slug_for in scripts/l2d.py. */
export function slugFor(skinId) {
  if (typeof skinId !== 'string' || !SKIN_ID.test(skinId)) throw new Error(`Unexpected skinId: ${skinId}`);
  return skinId.replace(/[@#]/g, '_');
}

/** models/<slug>/<first 12 hex of the bundle md5>. Mirrors folder_for in scripts/l2d.py. */
export function folderFor(skinId, md5) {
  if (typeof md5 !== 'string' || !MD5.test(md5)) throw new Error(`Unexpected bundle md5: ${md5}`);
  return `models/${slugFor(skinId)}/${md5.slice(0, 12)}`;
}

/** The version a skeleton declares: `skeleton.spine` in JSON, or the second header string in binary. */
export function skeletonVersion(bytes) {
  if (isJsonSkeleton(bytes)) {
    const match = /"spine"\s*:\s*"([^"]+)"/.exec(Buffer.from(bytes.subarray(0, 4096)).toString('utf8'));
    if (!match) throw new Error('JSON skeleton has no spine version');
    return match[1];
  }
  let offset = 0;
  const string = () => {
    let length = 0;
    let shift = 0;
    let byte;
    do {
      if (offset >= bytes.length || shift > 28) throw new Error('Invalid Spine binary header');
      byte = bytes[offset++];
      length += (byte & 127) * 2 ** shift;
      shift += 7;
    } while (byte & 128);
    if (length === 0) return '';
    if (offset + length - 1 > bytes.length) throw new Error('Invalid Spine binary header');
    const value = Buffer.from(bytes.subarray(offset, offset + length - 1)).toString('utf8');
    offset += length - 1;
    return value;
  };
  string(); // hash
  const version = string();
  if (!VERSION.test(version)) throw new Error(`Unrecognised Spine version: ${version}`);
  return version;
}

/** Width and height of a lossless WebP (VP8L chunk). Lossy WebP is rejected. */
export function webpSize(bytes) {
  if (bytes.length < 30 || bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WEBP') {
    throw new Error('Not a WebP file');
  }
  let offset = 12;
  while (offset + 8 <= bytes.length) {
    const type = bytes.toString('ascii', offset, offset + 4);
    const size = bytes.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (type === 'VP8L') {
      if (bytes[body] !== 0x2f) throw new Error('Bad VP8L signature');
      const bits = bytes.readUInt32LE(body + 1);
      return { width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1 };
    }
    if (type === 'VP8 ') throw new Error('WebP is lossy; textures must be lossless');
    offset = body + size + (size & 1);
  }
  throw new Error('WebP has no VP8L image');
}

const ATLAS_HEADER = /^\s*(size|format|filter|repeat|pma)\s*:/;

/**
 * The size: line of each atlas page, in order (null when a page has none). The 3.8 runtime
 * takes UVs from the loaded image's size, so each page image must have exactly this size.
 */
export function atlasPageSizes(text) {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
  const sizes = [];
  let expect = true;
  lines.forEach((line, i) => {
    if (!line.trim()) { expect = true; return; }
    if (!expect) return;
    expect = false;
    if (!ATLAS_HEADER.test(lines[i + 1] ?? '')) return;
    let size = null;
    for (let j = i + 1; j < lines.length && ATLAS_HEADER.test(lines[j]); j++) {
      const match = /^\s*size\s*:\s*(\d+)\s*,\s*(\d+)/.exec(lines[j]);
      if (match) { size = { width: Number(match[1]), height: Number(match[2]) }; break; }
    }
    sizes.push(size);
  });
  return sizes;
}

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const positiveInt = (value) => Number.isSafeInteger(value) && value > 0;

function fileShape(record, label, file) {
  if (!isObject(record)) throw new Error(`${label}: missing`);
  if (record.file !== file) throw new Error(`${label}: file must be ${file}, got ${record.file}`);
  if (!positiveInt(record.bytes)) throw new Error(`${label}: bytes missing`);
  if (typeof record.sha256 !== 'string' || !HEX64.test(record.sha256)) throw new Error(`${label}: sha256 missing`);
}

/** An MP3 starts with an ID3 tag or an MPEG audio frame sync. */
export function isMp3(bytes) {
  return bytes.length > 4 && (bytes.toString('ascii', 0, 3) === 'ID3' || (bytes[0] === 0xff && (bytes[1] & 0xe0) === 0xe0));
}

/** An entrance's camera: null (its prefab names none) or frames [t, centre x, centre y, visible
 *  height, roll] in skeleton units and degrees from t = 0, in time order, full-screen fades
 *  [{color: [r, g, b], keys: [[t, alpha]]}] within the entrance animation, and the hand-over
 *  colour ([r, g, b] or null). */
function cameraShape(camera, label, duration) {
  if (camera === null) return;
  if (!isObject(camera) || !Array.isArray(camera.frames) || !camera.frames.length || !Array.isArray(camera.fades) || !Object.hasOwn(camera, 'handover')) {
    throw new Error(`${label}: camera must be null or { frames, fades, handover }`);
  }
  const end = duration + 0.01;
  let last = -Infinity;
  camera.frames.forEach((frame, index) => {
    if (!Array.isArray(frame) || frame.length !== 5 || !frame.every(Number.isFinite) || !(frame[3] > 0) || Math.abs(frame[4]) > 180) {
      throw new Error(`${label}: camera.frames[${index}] must be [t, x, y, height > 0, roll in degrees]`);
    }
    if (!(frame[0] > last) || frame[0] > end) throw new Error(`${label}: camera.frames[${index}] is out of order or past the entrance (${frame[0]}s)`);
    last = frame[0];
  });
  if (camera.frames[0][0] !== 0) throw new Error(`${label}: camera.frames must start at 0`);
  if (camera.handover !== null && !(Array.isArray(camera.handover) && camera.handover.length === 3 && camera.handover.every((v) => Number.isFinite(v) && v >= 0 && v <= 1))) {
    throw new Error(`${label}: camera.handover must be null or [r, g, b] in 0-1`);
  }
  camera.fades.forEach((fade, index) => {
    if (!isObject(fade) || !Array.isArray(fade.color) || fade.color.length !== 3 || !fade.color.every((v) => Number.isFinite(v) && v >= 0 && v <= 1)) {
      throw new Error(`${label}: camera.fades[${index}].color must be [r, g, b] in 0-1`);
    }
    if (!Array.isArray(fade.keys) || !fade.keys.length) throw new Error(`${label}: camera.fades[${index}].keys missing`);
    let previous = -Infinity;
    fade.keys.forEach((key, k) => {
      if (!Array.isArray(key) || key.length !== 2 || !key.every(Number.isFinite) || key[1] < 0 || key[1] > 1 || !(key[0] > previous) || key[0] > end) {
        throw new Error(`${label}: camera.fades[${index}].keys[${k}] must be [t, alpha 0-1], in order, within the entrance`);
      }
      previous = key[0];
    });
  });
}

/** The shape of a skeleton + atlas + pages group: the illustration (name 'skeleton', pages
 *  'page') or its entrance ('entrance', 'entrance-page'). Returns its files. */
function skeletonShape(part, label, name, pagePrefix) {
  if (!isObject(part.skeleton) || !['binary', 'json'].includes(part.skeleton.format)) throw new Error(`${label}: skeleton.format must be binary or json`);
  fileShape(part.skeleton, `${label}: skeleton`, part.skeleton.format === 'json' ? `${name}.json` : `${name}.skel`);
  fileShape(part.atlas, `${label}: atlas`, `${name}.atlas`);
  if (!Array.isArray(part.textures) || !part.textures.length) throw new Error(`${label}: textures missing`);
  part.textures.forEach((texture, index) => {
    fileShape(texture, `${label}: textures[${index}]`, `${pagePrefix}${index}.webp`);
    if (!positiveInt(texture.width) || !positiveInt(texture.height)) throw new Error(`${label}: textures[${index}] width/height missing`);
  });
  if (!isObject(part.animations) || !Object.keys(part.animations).length ||
      !Object.values(part.animations).every((value) => typeof value === 'number' && Number.isFinite(value) && value >= 0)) {
    throw new Error(`${label}: animations must map names to durations`);
  }
  const bounds = part.bounds;
  if (!isObject(bounds) || !['x', 'y', 'width', 'height'].every((key) => Number.isFinite(bounds[key])) || bounds.width <= 0 || bounds.height <= 0) {
    throw new Error(`${label}: bounds missing`);
  }
  return [part.skeleton, part.atlas, ...part.textures];
}

/** A skeleton group's contents against its record: WebP sizes, format, version, atlas pages and
 *  (with `deep`) the runtime's reading of its animations and bounds. */
function checkSkeleton(part, label, contents, spineVersion, deep) {
  for (const texture of part.textures) {
    const size = webpSize(contents[texture.file]);
    if (size.width !== texture.width || size.height !== texture.height) throw new Error(`${label}: ${texture.file} is ${size.width}x${size.height}, model.json says ${texture.width}x${texture.height}`);
  }
  const skeletonBytes = contents[part.skeleton.file];
  if ((part.skeleton.format === 'json') !== isJsonSkeleton(skeletonBytes)) throw new Error(`${label}: skeleton format does not match its content`);
  if (skeletonVersion(skeletonBytes) !== spineVersion) throw new Error(`${label}: skeleton declares a different Spine version`);
  const atlasText = contents[part.atlas.file].toString('utf8');
  const pages = readAtlas(atlasText).pages.map((page) => page.name);
  const expectedPages = part.textures.map((texture) => texture.file);
  if (JSON.stringify(pages) !== JSON.stringify(expectedPages)) throw new Error(`${label}: atlas pages ${pages.join(', ')} do not match textures ${expectedPages.join(', ')}`);
  atlasPageSizes(atlasText).forEach((size, index) => {
    const texture = part.textures[index];
    if (size && (size.width !== texture.width || size.height !== texture.height)) {
      throw new Error(`${label}: ${texture.file} is ${texture.width}x${texture.height} but the atlas was packed at ${size.width}x${size.height}`);
    }
  });
  if (deep) {
    const found = inspectSkeleton(skeletonBytes, atlasText);
    if (found.spineVersion !== spineVersion) throw new Error(`${label}: runtime reads version ${found.spineVersion}`);
    if (JSON.stringify(found.animations) !== JSON.stringify(part.animations)) throw new Error(`${label}: animations differ from the skeleton: ${JSON.stringify(found.animations)}`);
    if (JSON.stringify(found.bounds) !== JSON.stringify(part.bounds)) throw new Error(`${label}: bounds differ from the skeleton: ${JSON.stringify(found.bounds)}`);
  }
}

/**
 * Checks one model folder: required fields, the folder name, every file's bytes and sha256,
 * the atlas pages, the WebP sizes, the declared Spine version and (with `deep`) that the
 * Spine 3.8 runtime reads the skeleton to the recorded animations and bounds.
 */
export async function validateModel(root, folder, { deep = true } = {}) {
  const label = folder;
  const model = JSON.parse(await readFile(path.join(root, folder, 'model.json'), 'utf8'));
  if (!isObject(model) || model.schemaVersion !== 1) throw new Error(`${label}: model.json schemaVersion must be 1`);
  const slug = slugFor(model.skinId);
  if (typeof model.dynIllustId !== 'string' || !/^dyn_illust_[A-Za-z0-9_#]+$/.test(model.dynIllustId)) throw new Error(`${label}: dynIllustId missing`);
  if (typeof model.spineVersion !== 'string' || !VERSION.test(model.spineVersion)) throw new Error(`${label}: spineVersion missing`);
  const source = model.source;
  if (!isObject(source) || source.server !== 'en' || typeof source.bundle !== 'string' || !/^arts\/dynchars\/[^/]+\.ab$/.test(source.bundle) ||
      typeof source.md5 !== 'string' || !MD5.test(source.md5) || typeof source.resVersion !== 'string' || !source.resVersion) {
    throw new Error(`${label}: source must give server, bundle, md5 and resVersion`);
  }
  if (folder !== folderFor(model.skinId, source.md5)) throw new Error(`${label}: folder must be ${folderFor(model.skinId, source.md5)} (slug ${slug})`);
  const files = skeletonShape(model, label, 'skeleton', 'page');
  if (model.premultipliedAlpha !== true) throw new Error(`${label}: premultipliedAlpha must be true`);
  // The site loops Idle and the bounds are framed from it. An entrance skeleton has only Start,
  // so this also catches the wrong skeleton picked from a bundle.
  if (!Object.hasOwn(model.animations, 'Idle')) throw new Error(`${label}: animations must include Idle (has ${Object.keys(model.animations).join(', ')})`);
  if (model.mixes !== undefined && (!Array.isArray(model.mixes) || !model.mixes.every((mix) =>
    isObject(mix) && typeof mix.from === 'string' && typeof mix.to === 'string' && Number.isFinite(mix.duration)))) {
    throw new Error(`${label}: mixes must be a list of {from, to, duration}`);
  }
  // The skin's dynEntranceId, recorded by the sync from skin_table: a skin that has one must
  // carry its entrance, and one without must not, so an entrance can never go missing quietly.
  if (!Object.hasOwn(model, 'dynEntranceId') || !(model.dynEntranceId === null || (typeof model.dynEntranceId === 'string' && ENTRANCE_ID.test(model.dynEntranceId)))) {
    throw new Error(`${label}: dynEntranceId must be the skin's dyn_entrance_ id or null`);
  }
  if (!Object.hasOwn(model, 'entrance')) throw new Error(`${label}: entrance must be present (null when the skin has none)`);
  const entrance = model.entrance;
  if ((entrance === null) !== (model.dynEntranceId === null)) {
    throw new Error(model.dynEntranceId ? `${label}: the skin has the entrance ${model.dynEntranceId} but model.json has none` : `${label}: entrance given for a skin without a dynEntranceId`);
  }
  if (entrance !== null) {
    if (!isObject(entrance)) throw new Error(`${label}: entrance must be an object`);
    files.push(...skeletonShape(entrance, `${label}: entrance`, 'entrance', 'entrance-page'));
    if (!Object.hasOwn(entrance.animations, 'Start')) throw new Error(`${label}: entrance animations must include Start (has ${Object.keys(entrance.animations).join(', ')})`);
    // The sync writes the camera the game plays the entrance through, or null when the prefab
    // names none; a camera it cannot decode fails the model, so the field is always there.
    if (!Object.hasOwn(entrance, 'camera')) throw new Error(`${label}: entrance.camera must be present (null when the prefab names no camera)`);
    cameraShape(entrance.camera, `${label}: entrance`, entrance.animations.Start);
    if (entrance.audio !== null) {
      fileShape(entrance.audio, `${label}: entrance.audio`, 'entrance.mp3');
      if (!(Number.isFinite(entrance.audio.duration) && entrance.audio.duration > 0)) throw new Error(`${label}: entrance.audio duration missing`);
      files.push(entrance.audio);
    }
  }

  const expectedNames = new Set(['model.json', ...files.map((file) => file.file)]);
  const present = await readdir(path.join(root, folder));
  const stray = present.filter((name) => !expectedNames.has(name));
  if (stray.length) throw new Error(`${label}: unexpected files ${stray.join(', ')}`);
  const contents = {};
  for (const file of files) {
    const filePath = path.join(root, folder, file.file);
    if (!existsSync(filePath)) throw new Error(`${label}: missing file ${file.file}`);
    const bytes = await readFile(filePath);
    if (bytes.length !== file.bytes) throw new Error(`${label}: ${file.file} is ${bytes.length} bytes, model.json says ${file.bytes}`);
    if (sha256(bytes) !== file.sha256) throw new Error(`${label}: ${file.file} sha256 does not match`);
    contents[file.file] = bytes;
  }
  checkSkeleton(model, label, contents, model.spineVersion, deep);
  if (entrance !== null) {
    checkSkeleton(entrance, `${label}: entrance`, contents, model.spineVersion, deep);
    if (entrance.audio !== null && !isMp3(contents['entrance.mp3'])) throw new Error(`${label}: entrance.mp3 is not an MP3`);
  }
  return model;
}

/** Every models/<slug>/<md5_12> folder on disk. */
export async function modelFolders(root) {
  const base = path.join(root, 'models');
  if (!existsSync(base)) return [];
  const folders = [];
  for (const slug of (await readdir(base)).sort()) {
    if (slug.startsWith('.')) continue;
    if (!(await stat(path.join(base, slug))).isDirectory()) throw new Error(`models/${slug} is not a folder`);
    for (const version of (await readdir(path.join(base, slug))).sort()) {
      if (version.startsWith('.')) continue;
      folders.push(`models/${slug}/${version}`);
    }
  }
  return folders;
}

/**
 * Checks sync-failures.json, when present: bundles that downloaded but could not be turned into a
 * model, which the sync skips until their md5 or its code changes.
 */
export async function validateFailures(root) {
  const file = path.join(root, 'sync-failures.json');
  if (!existsSync(file)) return 0;
  const document = JSON.parse(await readFile(file, 'utf8'));
  if (!isObject(document) || document.schemaVersion !== 1 || !isObject(document.failures)) {
    throw new Error('sync-failures.json must be { schemaVersion: 1, failures: {} }');
  }
  for (const [skinId, entry] of Object.entries(document.failures)) {
    slugFor(skinId);
    if (!isObject(entry) || typeof entry.md5 !== 'string' || !MD5.test(entry.md5) || typeof entry.code !== 'string' ||
        !/^[a-f0-9]{12}$/.test(entry.code) || typeof entry.resVersion !== 'string' || typeof entry.error !== 'string') {
      throw new Error(`sync-failures.json: ${skinId} must give md5, code, resVersion and error`);
    }
  }
  return Object.keys(document.failures).length;
}

/**
 * Validates manifest.json and every model folder (folders the manifest no longer names stay
 * published, so they are checked too). Returns counts.
 */
export async function validateRepository(root, { deep = true } = {}) {
  const manifest = JSON.parse(await readFile(path.join(root, 'manifest.json'), 'utf8'));
  if (!isObject(manifest) || manifest.schemaVersion !== 1 || manifest.server !== 'en' || !isObject(manifest.models)) {
    throw new Error('manifest.json must be { schemaVersion: 1, server: "en", resVersion, models: {} }');
  }
  if (manifest.resVersion !== null && typeof manifest.resVersion !== 'string') throw new Error('manifest.json: resVersion must be a string');
  const models = new Map();
  for (const folder of await modelFolders(root)) {
    if (!existsSync(path.join(root, folder, 'model.json'))) throw new Error(`${folder}: model.json missing`);
    models.set(folder, await validateModel(root, folder, { deep }));
  }
  const slugs = new Map();
  for (const [skinId, target] of Object.entries(manifest.models)) {
    const slug = slugFor(skinId);
    if (slugs.has(slug)) throw new Error(`${skinId} and ${slugs.get(slug)} share the folder name ${slug}`);
    slugs.set(slug, skinId);
    if (typeof target !== 'string' || !/^models\/[A-Za-z0-9_]+\/[a-f0-9]{12}\/model\.json$/.test(target)) throw new Error(`${skinId}: bad manifest path ${target}`);
    const folder = path.posix.dirname(target);
    const model = models.get(folder);
    if (!model) throw new Error(`${skinId}: ${target} does not exist`);
    if (model.skinId !== skinId) throw new Error(`${skinId}: ${target} belongs to ${model.skinId}`);
  }
  const failures = await validateFailures(root);
  return { listed: Object.keys(manifest.models).length, folders: models.size, failures };
}
