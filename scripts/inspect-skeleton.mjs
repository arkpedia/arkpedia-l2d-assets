#!/usr/bin/env node
// Usage: node scripts/inspect-skeleton.mjs <model folder> [skeleton | entrance | layers]
// skeleton (the default) or entrance: reads <name>.skel or <name>.json plus <name>.atlas from the
// folder with the vendored Spine 3.8 runtime and prints {spineVersion, format, animations, bounds,
// pages, regions} as JSON. layers: checks the folder's layers.json (written without its bounds) against
// the illustration skeleton and prints {bounds}, the frame the site opens on with the layers drawn.
// scripts/sync.py runs this on every new model before it writes model.json.
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { LAYERS_VERSION, layersShape } from './layers.mjs';
import { inspectLayers, inspectSkeleton } from './spine.mjs';

const folder = process.argv[2];
const mode = process.argv[3] ?? 'skeleton';
if (!folder || !['skeleton', 'entrance', 'layers'].includes(mode)) {
  console.error('Usage: node scripts/inspect-skeleton.mjs <model folder> [skeleton | entrance | layers]');
  process.exit(2);
}
const name = mode === 'layers' ? 'skeleton' : mode;
const binary = path.join(folder, `${name}.skel`);
const json = path.join(folder, `${name}.json`);
const skeletonPath = existsSync(binary) ? binary : json;
try {
  const skeleton = readFileSync(skeletonPath);
  const atlas = readFileSync(path.join(folder, `${name}.atlas`), 'utf8');
  let result;
  if (mode === 'layers') {
    const doc = JSON.parse(readFileSync(path.join(folder, 'layers.json'), 'utf8'));
    // A file with effects is written in the current format (a file of layersVersion 1 has none).
    const version = Object.hasOwn(doc, 'effectTextures') ? LAYERS_VERSION : 1;
    if (doc.bounds !== null || (version >= 2 && doc.effectBounds !== null)) throw new Error('layers.json already has its bounds');
    // Everything but the bounds this computes must already be right.
    const unit = { x: 0, y: 0, width: 1, height: 1 };
    layersShape({ ...doc, bounds: unit, ...(version >= 2 ? { effectBounds: unit } : {}) }, 'layers.json', version);
    result = inspectLayers(skeleton, atlas, doc, 'layers.json');
  } else {
    result = inspectSkeleton(skeleton, atlas);
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
} catch (error) {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
}
