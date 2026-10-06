# Artwork and software

Arknights names, characters, artwork and the Spine models in `models/` belong to Hypergryph, Yostar and their respective rights holders. This unofficial fan repository holds them so Arkpedia can show each outfit's animated art, for reference. It does not grant any rights to that material.

## Where each file comes from

Every model folder comes from the Arknights Global (EN) Android client:

- The list of skins with dynamic art is the EN `skin_table.json` from [ArknightsAssets/ArknightsGamedata](https://github.com/ArknightsAssets/ArknightsGamedata).
- The files come from that skin's asset bundle (`arts/dynchars/<id>.ab`) on Yostar's client asset CDN, as named by the client's own `hot_update_list.json`. Each `model.json` records the bundle path, its md5 and the client `resVersion` under `source`.
- The skeleton (`skeleton.skel` or `skeleton.json`) is the bundle's bytes, unchanged.
- The atlas (`skeleton.atlas`) is the bundle's bytes with only the page name lines renamed to `page0.webp`, `page1.webp`, ...
- Each `page<N>.webp` is the bundle's Android page texture, decoded and stored as lossless WebP with premultiplied alpha. The client ships a page in one of two ways:
  - As one RGBA texture (ASTC) with straight alpha. It is premultiplied here.
  - As an RGB texture (ETC) plus a separate `[alpha]` mask. The game's RGB is already premultiplied, so the mask becomes the alpha channel and the colour is kept exactly as shipped.

  Before a page is written, the sync measures it to confirm which of the two it is, and it skips any model whose page doesn't match. The Android textures are compressed, so these pages are the client's compressed art, not the original source files.
- `test/fixtures/pages/` holds three 64x64 crops of the same kind of page textures, used by the tests. Its README names the source of each.

No model files come from fan sites or mirrors.

## Software

- `vendor/spine-core-3.8/spine-core.js` is the official Spine runtime (spine-ts 3.8, commit `8b4844bd4b193ba9e54487ed397a777993cbad56` of [EsotericSoftware/spine-runtimes](https://github.com/EsotericSoftware/spine-runtimes)). It is covered by the Spine Runtimes License in `vendor/spine-core-3.8/LICENSE`, not by this repository's licence. Using it requires a Spine Editor licence, and only a Spine licensee may modify it. The Arkpedia owner holds a Spine Essential licence. The file is kept unmodified.
- The sync, inspection and validation scripts in `scripts/` and `test/` were written for this repository and are under the MIT licence in `LICENSE`. That licence covers only that code, not the game assets and not the Spine runtime.

Rights holders can ask for a correction or removal through this repository's issues.
