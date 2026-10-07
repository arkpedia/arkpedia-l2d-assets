#!/usr/bin/env node
// The particle coverage of every model folder under a root (the repository, or a trial export): how many of
// the systems the game draws each export carries (scripts/particles.mjs particleCoverage), checked against
// the baseline in particle-coverage.json; `--write` records the root's counts as the baseline (the folders it
// has; others are kept).
//
//   node scripts/particle_coverage.mjs [<root with models/>] [--write]
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkCoverage, COVERAGE_FILE, particleCoverage, PARTICLES_FILE } from './particles.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const root = path.resolve(args.find((a) => !a.startsWith('--')) ?? repo);
const baselinePath = path.join(repo, COVERAGE_FILE);
const baseline = existsSync(baselinePath) ? JSON.parse(readFileSync(baselinePath, 'utf8')) : { models: {} };

/** { folder: counts } for every folder under root/models whose layers.json lists its particles (an export with them). */
export function coverageOf(base) {
  const out = {};
  const models = path.join(base, 'models');
  for (const slug of existsSync(models) ? readdirSync(models).sort() : []) {
    for (const version of readdirSync(path.join(models, slug)).sort()) {
      const folder = `models/${slug}/${version}`;
      const layersPath = path.join(base, folder, 'layers.json');
      if (!existsSync(layersPath)) continue;
      const layers = JSON.parse(readFileSync(layersPath, 'utf8'));
      if (!Object.hasOwn(layers, 'particles')) continue;
      const particles = layers.particles ? JSON.parse(readFileSync(path.join(base, folder, PARTICLES_FILE), 'utf8')) : null;
      out[folder] = particleCoverage(layers, particles);
    }
  }
  return out;
}

export function totals(models) {
  const sum = { drawn: 0, exported: 0, v1: 0 };
  for (const counts of Object.values(models)) for (const key of Object.keys(sum)) sum[key] += counts[key];
  return sum;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const found = coverageOf(root);
  let below = 0;
  for (const [folder, counts] of Object.entries(found)) {
    try {
      checkCoverage(counts, baseline.models[folder], folder);
    } catch (error) {
      below++;
      console.log(error.message);
    }
  }
  const sum = totals(found);
  const share = (n) => (sum.drawn ? `${(100 * n / sum.drawn).toFixed(1)}%` : '-');
  console.log(`${Object.keys(found).length} folders with particles: ${sum.drawn} systems drawn, ${sum.exported} exported (${share(sum.exported)}), ${sum.v1} for the first release (${share(sum.v1)}); ${below} below the baseline`);
  if (args.includes('--write')) {
    const models = { ...baseline.models, ...found };
    const sorted = Object.fromEntries(Object.keys(models).sort().map((k) => [k, models[k]]));
    writeFileSync(baselinePath, `${JSON.stringify({ schemaVersion: 1, totals: totals(sorted), models: sorted }, null, 1)}\n`);
    console.log(`wrote ${COVERAGE_FILE}`);
  } else if (below) {
    process.exit(1);
  }
}
