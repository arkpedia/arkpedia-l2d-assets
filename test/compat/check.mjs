// The deployed site readers against layers.json files that carry particles: they must not notice them.
//
// dynamicLayers-v1.ts, -v2.ts and -v3.ts are byte-for-byte copies of the site's lib/skins/dynamicLayers.ts
// (arkpedia/arkpedia): v1 at main 54995c2a (www until 2026-10-07), v2 at aaddab47 (the shader port, www on
// 2026-10-07), v3 at e5762c0c (tilted meshes, layersVersion 3: www from release 666dcbc2 on, main d20c73e5
// still). Any of them may still run in a tab opened before a release. Each reader, given a layers.json with
// particles and the same file without them, must:
// - not throw;
// - skip exactly one more entry per particle run (an entry it does not know);
// - list the same textures (so fetch the same files) and frame the same bounds;
// - draw the same parts and layers, by name, in order.
// The particle file itself must be one the asset repo's reader accepts in full (scripts/particles.mjs:
// every system understood, none skipped).
//
//   node --experimental-strip-types test/compat/check.mjs <root with models/> [--baseline <root with models/>] [--inject]
//
// Without --baseline the file "without particles" is the same file with its particle runs, pointer and
// reasons taken out; with it, the baseline export's layers.json for the same folder (an export without
// --particles), which also proves the particle export leaves the layers as they were. --inject gives a file
// without particles three runs (first, middle and last in its draw list), a pointer and reasons, as the
// readers would meet them, and a file that carries particles (layersVersion 4) is checked as it is: the Check
// runs it over every committed folder (test/compat.test.mjs). The summary's `injected` counts the files given
// runs, `exported` the files that carry the `particles` member (pointer or null) and `exportedRuns` their runs.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { LAYERS_VERSION, layersShape } from '../../scripts/layers.mjs';
import { PARTICLES_FILE } from '../../scripts/particles.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const readers = {
  v1: (await import(path.join(here, 'dynamicLayers-v1.ts'))).parseDynamicLayers,
  v2: (await import(path.join(here, 'dynamicLayers-v2.ts'))).parseDynamicLayers,
  v3: (await import(path.join(here, 'dynamicLayers-v3.ts'))).parseDynamicLayers,
};

/** `doc` as if it had particles: three runs, a pointer and an empty list of reasons. */
export function withInjectedParticles(doc) {
  const out = structuredClone(doc);
  const middle = Math.floor(out.draw.length / 2);
  out.draw = [{ particles: [0] }, ...out.draw.slice(0, middle), { particles: [1] }, ...out.draw.slice(middle), { particles: [2] }];
  out.particles = { file: PARTICLES_FILE, bytes: 1, sha256: '0'.repeat(64), version: 1 };
  out.omitted = { ...out.omitted, particleReasons: [] };
  return out;
}

/** The same layers.json without particles: no runs, no pointer, no reasons (and omitted.particles 0). */
export function withoutParticles(doc) {
  const out = structuredClone(doc);
  delete out.particles;
  out.draw = out.draw.filter((entry) => !Object.hasOwn(entry, 'particles'));
  if (out.omitted) delete out.omitted.particleReasons;
  return out;
}

const drawn = (parsed) => parsed.draw.map((e) => (e.kind === 'part' ? `#part ${e.index}` : e.layer.name));

/** Problems a reader shows with `doc` against `before` (the same file without particles). */
export function compare(doc, before) {
  const problems = [];
  const runs = doc.draw.filter((entry) => Object.hasOwn(entry, 'particles')).length;
  for (const [name, parse] of Object.entries(readers)) {
    let now, then;
    try {
      then = parse(before);
    } catch (error) {
      problems.push(`${name} reader rejects the file without particles too (${error.message}); not compared`);
      continue;
    }
    try {
      now = parse(doc);
    } catch (error) {
      problems.push(`${name} reader throws: ${error.message}`);
      continue;
    }
    if (now.skipped !== then.skipped + runs) problems.push(`${name} reader skipped ${now.skipped}, expected ${then.skipped} + ${runs} runs`);
    const textures = (p) => JSON.stringify([...p.textures.map((t) => t.file), ...(p.effectTextures ?? []).map((t) => t.file)]);
    if (textures(now) !== textures(then)) problems.push(`${name} reader fetches other textures`);
    if (JSON.stringify(now.bounds) !== JSON.stringify(then.bounds)) problems.push(`${name} reader frames ${JSON.stringify(now.bounds)}, not ${JSON.stringify(then.bounds)}`);
    if (JSON.stringify(drawn(now)) !== JSON.stringify(drawn(then))) problems.push(`${name} reader draws other parts or layers`);
  }
  return { problems, runs };
}

function folders(root) {
  const out = [];
  const models = path.join(root, 'models');
  for (const slug of readdirSync(models).sort()) {
    for (const version of readdirSync(path.join(models, slug)).sort()) out.push(path.join('models', slug, version));
  }
  return out;
}

async function main() {
  const args = process.argv.slice(2);
  const root = args[0];
  const baseline = args.includes('--baseline') ? args[args.indexOf('--baseline') + 1] : null;
  const inject = args.includes('--inject');
  if (!root) {
    console.error('Usage: node --experimental-strip-types test/compat/check.mjs <root with models/> [--baseline <root with models/>]');
    process.exit(2);
  }
  let files = 0, injected = 0, exported = 0, withParticles = 0, runs = 0, exportedRuns = 0, systems = 0, failures = 0;
  for (const folder of folders(root)) {
    const file = path.join(root, folder, 'layers.json');
    if (!existsSync(file)) continue;
    files++;
    let doc = JSON.parse(readFileSync(file, 'utf8'));
    if (inject && !Object.hasOwn(doc, 'particles')) {
      injected++;
      const result = compare(withInjectedParticles(doc), doc);
      runs += result.runs;
      if (result.problems.length) {
        failures++;
        console.log(`${folder} (particles injected): ${result.problems.join('; ')}`);
      }
      continue;
    }
    const problems = [];
    if (Object.hasOwn(doc, 'particles')) exported++;
    let before = withoutParticles(doc);
    if (baseline) {
      const other = path.join(baseline, folder, 'layers.json');
      if (!existsSync(other)) problems.push('no baseline file');
      else before = JSON.parse(readFileSync(other, 'utf8'));
    }
    if (doc.particles) {
      withParticles++;
      // The asset repo's reader: the whole particle file, every system, or it throws.
      try {
        const particles = JSON.parse(readFileSync(path.join(root, folder, PARTICLES_FILE), 'utf8'));
        layersShape({ ...doc }, folder, LAYERS_VERSION, particles);
        systems += particles.systems.length;
      } catch (error) {
        problems.push(`the particle reader rejects it: ${error.message}`);
      }
    }
    const result = compare(doc, before);
    runs += result.runs;
    if (Object.hasOwn(doc, 'particles')) exportedRuns += result.runs;
    problems.push(...result.problems);
    if (baseline && JSON.stringify(withoutParticles(doc)) !== JSON.stringify(withoutParticles(before))) {
      const a = withoutParticles(doc), b = withoutParticles(before);
      const keys = Object.keys({ ...a, ...b }).filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
      // omitted.particles counts renderers without particles and systems left out with them.
      const omitted = keys.includes('omitted') && JSON.stringify({ ...a.omitted, particles: 0 }) === JSON.stringify({ ...b.omitted, particles: 0 });
      const real = keys.filter((k) => !(k === 'omitted' && omitted));
      if (real.length) problems.push(`layers.json differs from the baseline beyond particles: ${real.join(', ')}`);
    }
    if (problems.length) {
      failures++;
      console.log(`${folder}: ${problems.join('; ')}`);
    }
  }
  console.log(JSON.stringify({ files, injected, exported, withParticles, runs, exportedRuns, systems, failures }));
  process.exit(failures ? 1 : 0);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
