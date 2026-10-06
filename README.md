# Arkpedia dynamic art assets

Animated outfit art for Arkpedia: the dynamic illustrations ("dynamic art") of the Arknights Global client, as Spine 3.8 models the site's focused artwork view can play. Stats, skin records and everything else stay in `arkpedia-data`; this repository holds only the animation files and where each came from.

Scope: every skin in the EN `skin_table.json` that has a `dynIllustId` (88 as of client `26-09-23-17-49-43_b9cc4a`: 64 outfits and 24 default Elite 2 artworks). The 14 outfits with a `dynEntranceId` also carry their entrance: the sequence the game plays before the illustration, with its soundtrack. CN-only skins, `sp_` variants and the client's Unity particle effects and camera moves are not included.

## Layout

```
manifest.json
sync-failures.json                          (bundles that could not be built, see below)
models/<slug>/<md5_12>/model.json
models/<slug>/<md5_12>/skeleton.skel        (or skeleton.json)
models/<slug>/<md5_12>/skeleton.atlas
models/<slug>/<md5_12>/page0.webp           (page1.webp, ... for multi-page atlases)
models/<slug>/<md5_12>/entrance.skel        (or entrance.json; only for a skin with a dynEntranceId)
models/<slug>/<md5_12>/entrance.atlas
models/<slug>/<md5_12>/entrance-page0.webp
models/<slug>/<md5_12>/entrance.mp3         (its soundtrack, 160 kbit/s)
```

- `slug` is the skinId with `@` and `#` replaced by `_` (`char_113_cqbw@epoque#7` becomes `char_113_cqbw_epoque_7`).
- `md5_12` is the first 12 hex digits of the source bundle's md5 in the client's `hot_update_list.json`. When the game ships a changed bundle it gets a new folder. A folder never changes once written and is never deleted, so a URL pinned to a commit keeps working.

`manifest.json` names the current folder of each skin:

```json
{ "schemaVersion": 1, "server": "en", "resVersion": "<client list the newest entries came from>",
  "models": { "char_1044_hsgma2#2": "models/char_1044_hsgma2_2/b1259edb8fff/model.json" } }
```

`model.json` describes one folder:

| Field | Meaning |
| --- | --- |
| `skinId`, `dynIllustId` | The skin_table ids. Elite 2 art is `char_X#2`; outfits are `char_X@group#N`. |
| `spineVersion` | Read from the skeleton itself (always `3.8.99` so far). Needs the Spine 3.8 runtime. |
| `skeleton` | `skeleton.skel` (`format: "binary"`) or `skeleton.json` (`format: "json"`), with `bytes` and `sha256`. |
| `atlas` | `skeleton.atlas`, with `bytes` and `sha256`. |
| `textures` | One entry per atlas page, in order: `page<N>.webp`, `width`, `height`, `bytes`, `sha256`. |
| `premultipliedAlpha` | Always `true`: render with premultiplied alpha. |
| `animations` | Animation name to duration in seconds (3 decimals). Every model has `Idle`, which the validator requires. The other names differ per model: `Interact` and `Special` are common, and a few have `Start`. |
| `bounds` | `x`, `y`, `width`, `height` of the setup pose with `Idle` applied at time 0, in skeleton units. Frame the camera from this; the skeletons' own width and height are 0. |
| `mixes` | The game's own crossfade table (`from`, `to`, `duration` in seconds), when the bundle has one. |
| `dynEntranceId` | The skin_table's `dynEntranceId`, or `null`. The validator requires `entrance` exactly when this is set, so an entrance cannot go missing quietly. |
| `entrance` | `null`, or the entrance: `skeleton`, `atlas`, `textures` (`entrance-page<N>.webp`), `animations` (one, `Start`, 8-23 s), `bounds`, `camera` and `audio` (`entrance.mp3` with `duration`, or `null`). It is drawn in the illustration's own coordinates. The game then plays the illustration's short `Start` and loops `Idle`. |
| `entrance.camera` | The camera the game plays the entrance through, or `null` when its prefab names none: `frames`, `[t, centre x, centre y, visible height, roll]` in skeleton units and degrees from 0 s (sampled at 30 fps, points a straight line reproduces within half a unit dropped; interpolate linearly), and `fades`, the full-screen quads that hide its cuts and flash at the end, as `{color: [r, g, b], keys: [[t, alpha]]}`, and `handover`, the colour the controller hands over to the illustration through (`_params.fadeColor`). Decoded by `scripts/entrance_camera.py` from the prefab's Animators (Mecanim streamed, dense and constant curves) through the whole transform chain; a camera it cannot reproduce fails the model. Particle systems and other effects are not included. |
| `source` | `server` (`en`), the client `bundle` path, its full `md5` and the `resVersion` it was downloaded from. |

## How the files are made

`scripts/sync.py` runs daily on GitHub Actions (`.github/workflows/sync.yml`) and can be started by hand with a `limit` or a list of skinIds.

1. It reads the EN `skin_table.json` from ArknightsAssets/ArknightsGamedata and the Global client's network config, version file and `hot_update_list.json`.
2. For each skin with a `dynIllustId` whose bundle (`arts/dynchars/<id>.ab`) the list carries, it skips the bundle if a folder for that md5 already exists. Otherwise it downloads the `.dat` from the client's asset CDN (GET only, one at a time, a 3 second pause between downloads) and checks its size and md5 against the list.
3. It decodes the bundle with UnityPy (plus the LZ4AK patch Arknights bundles need). File names come from the bundle, never from the id. The skeleton is the one the illustration prefab (`dyn/arts/dynchars/.../<id>.prefab` in the bundle's container) plays, and its SkeletonDataAsset links it to its atlas. Names alone are not enough: Kal'tsit's boc#6 bundle has an entrance skeleton with exactly the same name as the illustration. Names are used only for a bundle without that prefab, and then entrance (`_Start`, `_Start#N`) and portrait skeletons are left out. Particle textures and effect masks are always ignored. A skin with a `dynEntranceId` also takes the skeleton its entrance prefab (`dyn/arts/dyncharstart/.../<dynEntranceId>.prefab`) plays, never one picked by name, and the soundtrack under `.../dynentrance/<dynEntranceId>/`, exported by UnityPy and encoded as MP3 (lameenc). A missing entrance prefab fails the model.
4. Skeleton bytes are written unchanged. In the atlas only the page name lines change, to `page0.webp`, `page1.webp`, ... (the originals contain `#`, which breaks URLs). Each page is saved as premultiplied, lossless WebP at exactly the size the atlas was packed at. The client ships a page in one of two ways, and each needs different handling:
   - **One RGBA texture** (ASTC, e.g. Hoshiguma's Elite 2): straight alpha with colour under transparent texels. It is premultiplied here. A few ship already premultiplied (Goldenglow's summer#12); both measurements say so, and they are kept as shipped.
   - **An RGB texture plus a separate `[alpha]` mask** (ETC, e.g. Ch'en's Elite 2, Nian's Elite 2): the game's RGB is already premultiplied. The mask becomes the alpha channel and the colour is kept as shipped. Premultiplying again would darken every soft edge and glow.

   Before writing a page, the sync measures it: the colour under fully transparent texels, and how often colour exceeds alpha at alpha 16-63. Straight pages measure about 120-156 and 80-97%; masked pages measure under 0.1 and 5-14%. A page that doesn't match how it was shipped, or measures in between, fails the model instead of being written wrong. The result for each page is printed in the log, in `.cache/sync-report.json` (`pages`) and in the run summary.
5. `scripts/inspect-skeleton.mjs` reads the skeleton with the vendored Spine 3.8 runtime (`vendor/spine-core-3.8/`) to record the animations and bounds. A skeleton without an `Idle` animation fails the model, because an entrance skeleton has only `Start`. Then the folder is moved into place and `manifest.json` is updated.

A model that fails at any step is skipped and reported. The run still commits the models that worked, then shows as failed so someone looks. The manifest only ever names complete folders. Before anything is pushed the workflow runs the validator and the tests, because a push made with the workflow token does not start the Check workflow.

When a bundle downloads and verifies but can't be turned into a model, the sync records it in `sync-failures.json` (skinId, bundle md5, a fingerprint of the sync code, and the error) and commits the file. Later runs skip that bundle until the game ships a new one (a new md5) or the sync code changes, so a broken bundle isn't downloaded again every day. Skipped bundles show as warnings, and only a new failure marks the run as failed. To retry one by hand, run Sync with it in `only`, or with `retry_failed` for all of them. A download that fails or doesn't match the client list is never recorded, since it may work the next day.

`.github/workflows/check.yml` runs on every push and pull request. It installs the same Python packages as the sync, runs the Node and Python tests, and runs `npm run validate`, which checks every folder on disk (including old ones the manifest no longer names). It checks that each file exists with the recorded bytes and sha256, that the required fields are present, that the folder name matches the skinId and md5, that the atlas pages match the texture list and sizes, and that the Spine runtime re-reads each skeleton to the recorded animations and bounds.

## Run it locally

Python 3.12 and Node.js 22. The scripts have no npm dependencies.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/sync.py --dry-run                      # what would be fetched, and how much
.venv/bin/python scripts/sync.py --only 'char_1044_hsgma2#2'    # quote ids: they contain # and @
.venv/bin/python scripts/sync.py --limit 10
.venv/bin/python scripts/sync.py --retry-failed                 # also retry bundles in sync-failures.json
npm run validate
npm test
.venv/bin/python -m unittest discover -s test -p 'test_*.py'
```

Run one sync at a time; the workflow's concurrency group makes sure two never overlap on GitHub.

## Using the files

Load `manifest.json` and the files through URLs pinned to a commit of this repository (`https://raw.githubusercontent.com/arkpedia/arkpedia-l2d-assets/<commit>/...`), never the moving `main`. Raw URLs send `Access-Control-Allow-Origin: *`. A JSON skeleton is fetched as text and a binary one as an ArrayBuffer; branch on `skeleton.format`. Render with the Spine 3.8 runtime and `premultipliedAlpha: true`.

## Size

The first full sync downloads 88 bundles, 434 MB (by the client list's `totalSize`). The repository will hold about 125 MB of skeletons and atlases plus 290 to 440 MB of lossless WebP pages, about 0.4 to 0.6 GB in all. Later runs only fetch bundles the game changes.

See [NOTICE.md](NOTICE.md) for ownership and provenance, and [vendor/spine-core-3.8/README.md](vendor/spine-core-3.8/README.md) for the Spine runtime's licence.
