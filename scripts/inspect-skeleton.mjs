#!/usr/bin/env node
// Usage: node scripts/inspect-skeleton.mjs <model folder>
// Reads skeleton.skel or skeleton.json plus skeleton.atlas from the folder with the vendored
// Spine 3.8 runtime and prints {spineVersion, format, animations, bounds, pages, regions} as JSON.
// scripts/sync.py runs this on every new model before it writes model.json.
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { inspectSkeleton } from './spine.mjs';

const folder = process.argv[2];
if (!folder) {
  console.error('Usage: node scripts/inspect-skeleton.mjs <model folder>');
  process.exit(2);
}
const binary = path.join(folder, 'skeleton.skel');
const json = path.join(folder, 'skeleton.json');
const skeletonPath = existsSync(binary) ? binary : json;
try {
  const result = inspectSkeleton(readFileSync(skeletonPath), readFileSync(path.join(folder, 'skeleton.atlas'), 'utf8'));
  process.stdout.write(`${JSON.stringify(result)}\n`);
} catch (error) {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
}
