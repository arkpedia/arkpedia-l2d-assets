#!/usr/bin/env node
// Usage: node scripts/inspect-skeleton.mjs <model folder> [skeleton | entrance]
// Reads <name>.skel or <name>.json plus <name>.atlas (name: skeleton, the default, or entrance) from the folder with the vendored
// Spine 3.8 runtime and prints {spineVersion, format, animations, bounds, pages, regions} as JSON.
// scripts/sync.py runs this on every new model before it writes model.json.
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { inspectSkeleton } from './spine.mjs';

const folder = process.argv[2];
const name = process.argv[3] ?? 'skeleton';
if (!folder || !['skeleton', 'entrance'].includes(name)) {
  console.error('Usage: node scripts/inspect-skeleton.mjs <model folder> [skeleton | entrance]');
  process.exit(2);
}
const binary = path.join(folder, `${name}.skel`);
const json = path.join(folder, `${name}.json`);
const skeletonPath = existsSync(binary) ? binary : json;
try {
  const result = inspectSkeleton(readFileSync(skeletonPath), readFileSync(path.join(folder, `${name}.atlas`), 'utf8'));
  process.stdout.write(`${JSON.stringify(result)}\n`);
} catch (error) {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
}
