# spine-core 3.8 (vendored, unmodified)

`spine-core.js` is the official Spine runtime core for TypeScript/JavaScript, copied byte for byte from
[EsotericSoftware/spine-runtimes](https://github.com/EsotericSoftware/spine-runtimes), branch `3.8`,
commit `8b4844bd4b193ba9e54487ed397a777993cbad56`, file `spine-ts/build/spine-core.js`.
`LICENSE` is `spine-ts/LICENSE` from the same commit (the Spine Runtimes License Agreement).

| File | Bytes | SHA-256 |
| --- | --- | --- |
| `spine-core.js` | 299442 | `f1e0a31b9906e4d4daf2733857d21381ddbbe75adec7f4d83e1cc9b2b070dfc1` |
| `LICENSE` | 1454 | `6142ee6cc2c03d3a918793e4750ae772bd3755c534d4a35e559e301acf51ec39` |

The test suite checks these hashes, so an accidental edit fails CI.

**Licence.** The Spine Runtimes are not open source. Using or changing them requires a Spine Editor
licence (see `LICENSE`). Only a Spine licensee may modify this file. The Arkpedia owner holds a Spine
Essential licence. Contributors without their own Spine licence must not change it; the scripts in this
repository only load it as is.

**Why 3.8.** Every Arknights dynamic illustration is a Spine 3.8.99 skeleton. The 4.x runtimes on npm
cannot read 3.8 data, so the importer uses this build to read each skeleton headlessly
(`scripts/spine.mjs`) and record its animations and bounds.
