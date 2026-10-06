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

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const positiveInt = (value) => Number.isSafeInteger(value) && value > 0;

function fileShape(record, label, file) {
  if (!isObject(record)) throw new Error(`${label}: missing`);
  if (record.file !== file) throw new Error(`${label}: file must be ${file}, got ${record.file}`);
  if (!positiveInt(record.bytes)) throw new Error(`${label}: bytes missing`);
  if (typeof record.sha256 !== 'string' || !HEX64.test(record.sha256)) throw new Error(`${label}: sha256 missing`);
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
  if (!isObject(model.skeleton) || !['binary', 'json'].includes(model.skeleton.format)) throw new Error(`${label}: skeleton.format must be binary or json`);
  fileShape(model.skeleton, `${label}: skeleton`, model.skeleton.format === 'json' ? 'skeleton.json' : 'skeleton.skel');
  fileShape(model.atlas, `${label}: atlas`, 'skeleton.atlas');
  if (!Array.isArray(model.textures) || !model.textures.length) throw new Error(`${label}: textures missing`);
  model.textures.forEach((texture, index) => {
    fileShape(texture, `${label}: textures[${index}]`, `page${index}.webp`);
    if (!positiveInt(texture.width) || !positiveInt(texture.height)) throw new Error(`${label}: textures[${index}] width/height missing`);
  });
  if (model.premultipliedAlpha !== true) throw new Error(`${label}: premultipliedAlpha must be true`);
  if (!isObject(model.animations) || !Object.keys(model.animations).length ||
      !Object.values(model.animations).every((value) => typeof value === 'number' && Number.isFinite(value) && value >= 0)) {
    throw new Error(`${label}: animations must map names to durations`);
  }
  const bounds = model.bounds;
  if (!isObject(bounds) || !['x', 'y', 'width', 'height'].every((key) => Number.isFinite(bounds[key])) || bounds.width <= 0 || bounds.height <= 0) {
    throw new Error(`${label}: bounds missing`);
  }
  if (model.mixes !== undefined && (!Array.isArray(model.mixes) || !model.mixes.every((mix) =>
    isObject(mix) && typeof mix.from === 'string' && typeof mix.to === 'string' && Number.isFinite(mix.duration)))) {
    throw new Error(`${label}: mixes must be a list of {from, to, duration}`);
  }

  const files = [model.skeleton, model.atlas, ...model.textures];
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
  for (const texture of model.textures) {
    const size = webpSize(contents[texture.file]);
    if (size.width !== texture.width || size.height !== texture.height) throw new Error(`${label}: ${texture.file} is ${size.width}x${size.height}, model.json says ${texture.width}x${texture.height}`);
  }
  const skeletonBytes = contents[model.skeleton.file];
  if ((model.skeleton.format === 'json') !== isJsonSkeleton(skeletonBytes)) throw new Error(`${label}: skeleton format does not match its content`);
  if (skeletonVersion(skeletonBytes) !== model.spineVersion) throw new Error(`${label}: skeleton declares a different Spine version`);
  const atlasText = contents[model.atlas.file].toString('utf8');
  const pages = readAtlas(atlasText).pages.map((page) => page.name);
  const expectedPages = model.textures.map((texture) => texture.file);
  if (JSON.stringify(pages) !== JSON.stringify(expectedPages)) throw new Error(`${label}: atlas pages ${pages.join(', ')} do not match textures ${expectedPages.join(', ')}`);
  if (deep) {
    const found = inspectSkeleton(skeletonBytes, atlasText);
    if (found.spineVersion !== model.spineVersion) throw new Error(`${label}: runtime reads version ${found.spineVersion}`);
    if (JSON.stringify(found.animations) !== JSON.stringify(model.animations)) throw new Error(`${label}: animations differ from the skeleton: ${JSON.stringify(found.animations)}`);
    if (JSON.stringify(found.bounds) !== JSON.stringify(model.bounds)) throw new Error(`${label}: bounds differ from the skeleton: ${JSON.stringify(found.bounds)}`);
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
  return { listed: Object.keys(manifest.models).length, folders: models.size };
}
