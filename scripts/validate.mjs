#!/usr/bin/env node
// Validates manifest.json and every model folder. `--shallow` skips re-reading skeletons
// with the Spine runtime (hashes, sizes and fields are always checked).
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateRepository } from './manifest.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const deep = !process.argv.includes('--shallow');
try {
  const { listed, folders, failures } = await validateRepository(root, { deep });
  console.log(`Validated ${listed} manifest entries and ${folders} model folders${deep ? ' (skeletons re-read with Spine 3.8)' : ''}, ${failures} recorded sync failures`);
} catch (error) {
  console.error(`Validation failed: ${error.message}`);
  process.exit(1);
}
