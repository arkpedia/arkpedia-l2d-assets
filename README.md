# Arkpedia dynamic art assets

Animated outfit art for Arkpedia: the dynamic illustrations ("dynamic art") of the Arknights Global client, and of the CN client for outfits Global does not have yet, as Spine 3.8 models the site's focused artwork view can play. Stats, skin records and everything else stay in `arkpedia-data`; this repository holds only the animation files and where each came from.

Scope: every skin in the EN `skin_table.json` that has a `dynIllustId` (88 as of client `26-09-23-17-49-43_b9cc4a`: 64 outfits and 24 default Elite 2 artworks), and every skin in the zh_CN one that Global's plan does not have, from the CN client (8 as of CN client `26-09-22-07-47-20_6c71fa`: 6 outfits and 2 Elite 2 artworks). When Global's client carries one of those, its Global bundle is built into a new folder and the manifest moves to it; the CN folder stays published. The outfits with a `dynEntranceId` also carry their entrance: the sequence the game plays before the illustration, with its soundtrack. Each model also carries the illustration prefab's own mesh layers (skies, windows, frames, glows the game draws with the skeleton) that can be drawn exactly as the game draws them, including those drawn with the game's effect shaders (flow distortion, dissolve, ramps, vertex disturbance), whose parameters and textures are carried as data. `sp_` variants, the client's particle systems and the effects no shader description covers are not included; `layers.json` records per model what was left out and why.

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
models/<slug>/<md5_12>/layers.json          (the illustration prefab's mesh layers, see below)
models/<slug>/<md5_12>/layer0.webp          (layer1.webp, ...: their textures)
```

- `slug` is the skinId with `@` and `#` replaced by `_` (`char_113_cqbw@epoque#7` becomes `char_113_cqbw_epoque_7`).
- `md5_12` is the first 12 hex digits of the source bundle's md5 in the client's `hot_update_list.json`. When the game ships a changed bundle it gets a new folder. A folder is never deleted, and its files change in one case only: when the layers' format grows (model.json's `layersVersion` is older than the sync's), the sync exports that folder's layers again from the same bundle, in place: `layers.json`, its `layer<N>.webp` and model.json's `layers` and `layersVersion`. The skeleton, atlas, pages, entrance and soundtrack stay byte for byte. Every file ever written stays at the commit that wrote it, so a URL pinned to a commit keeps working, and a reader of the older format keeps reading what it read before (see `layersVersion` below).

`manifest.json` names the current folder of each skin:

```json
{ "schemaVersion": 1, "server": "en", "resVersion": "<Global's client list>",
  "resVersions": { "en": "<Global's client list>", "cn": "<CN's client list>" },
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
| `layers` | `layers.json` with `bytes` and `sha256`, or `null` for a bundle without an illustration prefab (none so far). Always present. |
| `layersVersion` | What `layers.json` holds: `2`, plain layers and effect layers (below); absent or `1`, plain layers only (folders written before effects, until the sync exports their layers again). |
| `source` | `server` (`en` for Global, `cn` for CN), the client `bundle` path, its full `md5` and the `resVersion` it was downloaded from. |

### layers.json

The illustration prefab draws more than its skeleton: MeshRenderers next to it (mostly under `General Effects`) hold backdrops and effects, and groups the illustration controller switches with the animation that plays (`Interact/Special/Start/Idle Only Effects`). `scripts/layers.py` exports every one the site can draw exactly as the game does; everything else is listed in `omitted` with its reason, never drawn approximately. Written without indentation (it is mostly numbers).

| Field | Meaning |
| --- | --- |
| `schemaVersion` | `1` (layersVersion 2 only adds members a reader of 1 ignores or skips, so it stays 1). |
| `textures` | The textures plain layers draw, `layer<N>.webp` in order: `width`, `height`, `bytes`, `sha256`, `wrap` (`[u, v]`, each `repeat`, `clamp` or `mirror`), `opaque` (`[u0, v0, u1, v1]`, image-space UV of the part with alpha 8/255 or more, which `bounds` is fitted to). Lossless WebP of the texture as shipped, straight alpha: every shader a layer can have samples it straight (texture x 2 x vertex colour x colour, alpha clamped, blended `SrcAlpha`). The sync measures each one as it measures atlas pages and prints the result; it changes nothing. |
| `effectTextures` | layersVersion 2: the textures only effects sample (an effect layer's main texture, noise, dissolve, weight and ramp maps), records like `textures`, numbered on from them (`layer<N>.webp` throughout, one index space). A reader that does not draw effects never fetches them. |
| `bounds` | The frame the site opens on: the skeleton's bounds joined with the visible part (`opaque`) of every plain layer drawn at Idle's first frame, in skeleton units. Computed with the Spine runtime (bone followers placed from the posed skeleton) and re-checked by the validator. |
| `effectBounds` | layersVersion 2: the same with the effect layers drawn too, each where it shows at its first frame (its `visible`). |
| `separators` | The skeleton's separator slots (SkeletonRenderSeparator) that exist in the skeleton. |
| `draw` | Back to front, sorted as Unity sorts them: sorting layer, sorting order, render queue, distance (farther first), hierarchy order. Each entry is `{"part": k}`, the skeleton's slots from the k-th separator met in the current draw order to the next (a separator starts its part; one part `0` when the skeleton has no parts renderers), `{"layer": {...}}` (a plain layer), or, in layersVersion 2, `{"effect": {...}}` (a layer drawn with an effect shader; a reader of layersVersion 1 does not know the entry and leaves it out). |
| `omitted` | Counts of `particles`, `trails`, `skinned` meshes, `hidden` renderers (off and never switched on) and `holders` (effects loaded from the shared `arts/dynchars/effect.ab`, not fetched), and lists of `{name, reason}`: `custom` (a shader no description covers: UV rotation and custom vertex streams set by scripts and particle streams, grab passes, 3D lighting, sprite sheets, an unknown shader...), `externalTexture` (a texture in a shared bundle `shared-bundles.json` does not list or the client list does not carry) and `other` (unknown scripts or animated properties, such as a dissolve's `_Amount` driven by an Animator, meshes the export cannot read, layers an Erase mask drawn after them paints over...). |

A layer:

| Field | Meaning |
| --- | --- |
| `name` | The GameObject's name (with ` (n)` per material when a renderer has several). |
| `blend` | `alpha` (SrcAlpha, OneMinusSrcAlpha) or `add` (SrcAlpha, One). |
| `texture` | Index into `textures`. |
| `color` | `[r, g, b, a]`: the material colour x 2 x the shader's scales (opacity, a constant dissolve, Disturb Anchor's colour control); `null` when `animation` carries it per frame. |
| `vertices` | `x, y` pairs: skeleton units for a static layer; the bone follower's own units under `follow`; the mesh's own units under `animation`. |
| `uvs`, `triangles`, `colors` | UVs with the material's tiling applied and v flipped to image space; indices into the vertices; vertex colours `r, g, b, a` in 0-1, or `null` (white). |
| `follow` | `null`, or the BoneFollower that places the layer: `bone`, `xy`, `rotation`, `localScale`, `mirrored` (a mirrored parent negates the bone rotation), `parent` (`[a, b, c, d]`, skeleton units per follower unit), `position` (`[x, y]` used when `xy` is off) and `angle` (degrees, used when `rotation` is off). Each frame: `parent x R(rotation) x scale` then the bone's world position, where rotation is the bone's world rotation (negated when `mirrored`, plus 180 with `localScale` and a negative scaleX) and scale the bone's local scale with `localScale`, as spine-unity's BoneFollower does. |
| `animation` | `null`, or the layer's timeline from its Animators' default states: `length`, `loop`, `loopFrom` and `frames`, `[t, a, b, c, d, tx, ty, r, g, b, alpha, active, su, ou, sv, ov]`: the matrix from the mesh's units to skeleton units (to the follower's units under `follow`), the colour, whether it is switched on (0/1, stepped), and the UV map on the exported UVs (`u' = u su + ou`, `v' = v sv + ov`). Sampled at 30 fps and decimated; interpolate linearly. After `length` it wraps to `loopFrom` when `loop` is set, else holds. `states` (optional): `Interact`, `Special`, `Start`: the timeline played from that animation's start when the illustration controller's trigger (`OnInteract`...) puts the layer's Animator in another state; `Idle` restarts the default. |
| `scroll` | `null`, or `[u, v]` image-space UV units per second (the shader's `_MainUSpeed`/`_MainVSpeed` or `_UVTween`, or a UV scroll script's speeds; the script's semantics are inferred from its field names). |
| `only` | `null`, or the animation whose controller group the layer is in (`Idle`, `Interact`, `Special`, `Start`): drawn only while it plays, its timeline from that animation's start. |
| `delay` | Seconds after its clock starts before the layer appears (a `_delayTime` script on its chain), its timeline shifted by as much. |
| `approximated` | `null`, or what a reader that draws plain layers only draws differently from the game. Today only one case: a slight flow distortion (at most 0.06 UV and 40 texels: `FLOW_UNDISTORTED` in `scripts/layers.py`) drawn without its wobble, because leaving the layer out loses a backdrop (Ines's in Under the Flaming Dome, Rosmontis's sky). Such a layer also has `exact`. |
| `exact` | layersVersion 2, only beside `approximated`: `{uvs, shader}`, the same layer drawn exactly, as an effect layer is (the mesh's own UVs and the shader). A reader that knows the shader draws this instead; one that does not draws the approximation. |

An effect layer (`{"effect": {...}}`) has the plain layer's members except `scroll`, `approximated` and `exact` (the shader scrolls), plus `shader`, `visible` and `cull`. `visible` is where it shows at its first frame, `[u0, v0, u1, v1]` in its own UVs (its main texture's alpha, or for an additive one colour x alpha, times its colour and dissolves, sampled over its UVs, undistorted), or `null` when it shows over less than a tenth of them (scattered sparks and stars, or nothing yet; `VISIBLE_COVERAGE` in `scripts/layers.py`): the frame (`effectBounds`) is fitted to it, so a glow faded down, a sky dissolved to wisps or a field of stars does not stretch it. `cull` is `0`, or for a layer that moves the faces it culls as it is drawn (`1` front, `2` back; Unity's front faces wind clockwise on screen; a static layer's culled faces are dropped at export). A plain material whose layer culls faces while it moves is written as an effect layer with a shader that adds nothing, since a reader of plain layers would draw both faces. Its `uvs` are the mesh's own, in Unity's UV space (v up): every texture, the main one included, has its own tiling in `shader`, and v is flipped only when a texture is sampled (image rows run down). `texture` is its main texture, or `null` when the material binds none (Unity's white). Its `color` is the material colour x 2 x the shader's scales, as a plain layer's, except in the `noise` family (Disturb2 has no x2). `scripts/effects.py` reads each shader's parameters; the maths is the shaders' GLSL (GLES3, from the client's `[uc]shaders.ab`).

`shader` (every map is `{texture, st, speed, scroll}`: a texture index, its tiling `[sx, sy, ox, oy]` applied to the mesh UVs, the shader's own scroll `speed` added as `fract(t x speed)`, and a UV scroll script's `scroll` added as `t x scroll`, wrapped only where the texture repeats; Unity UV units per second):

| Member | Meaning |
| --- | --- |
| `family` | `particle`: the Torappu particle shaders (Disturb(CustomData), Ram/Disturb(CustomData), Ram/VertexDisturb(CustomData), Disturb Anchor, Disturb, and Dissolve Add/AB, UVTween, Double, Double edge, Dissolve(CustomData)). `noise`: Disturb2. A reader skips a family or member it does not know. |
| `main` | `{st, speed, scroll, fract}`: the main texture's tiling and scrolls; `fract` samples at `fract(uv)` (the UVTween shaders). An animated layer's frames map the main coordinate as a plain layer's do (`su, ou, sv, ov` on image-space UVs). |
| `distort` | `null`, or the flow distortion: `maps` (one or two), each adding `(texture.r - anchor) x intensity` (`anchor`, `intensity`: `[u, v]`; `texture` null reads 0), the sum times `weight`'s red and green when there is one, plus `constant` (`[u, v]`). `space` `main`: added to the main UV after its tiling, times `main`, and to the first dissolve's UV times `dissolve` (Disturb, Ram, VertexDisturb). `space` `raw`: added to the mesh UV before every tiling (Disturb Anchor, Disturb). |
| `dissolve` | Up to two maps, each with `fract`, `amount` and `border`: alpha x `clamp((texture.r - amount + border x k) / border)`, `k = 1 - roundEven(amount + 0.5)`. A dissolve without a texture is a constant, already in `color`. |
| `edge` | `null`, or the Double edge shaders' edge colour: `{color, pow, epsilon}`; `e = (1 - smoothstep(f / color.a))^pow` mixes the colour towards `color` and its alpha towards `color.a x alpha` (`epsilon`: the AB shader's +0.001 on both sides and e at least 0.001). |
| `ramp` | `null`, or a map whose colour multiplies the texture's (Ram shaders). |
| `vertex` | `null`, or VertexDisturb's vertex noise: a map, `intensity` `[x, y, z]`, `weight` (a map or null) and `matrix` `[a, b, c, d, e, f]`: each vertex moves by `(texture.rgb - 0.5) x weight.r x intensity` in the mesh's own units, mapped into the vertices' units by `matrix`. |
| `animated` | The parameters its Animators drive, in order: `dissolve.<i>.amount`, `.border`, `.st`, `distort.maps.<i>.st`, `.intensity`, `distort.weight.st`, `ramp.st`, `vertex.st`, `vertex.intensity`, `vertex.weight.st`, `edge.color`. Their values follow the layer's frames (and its `states`' frames), after the 16 numbers every frame has: 1 number for an amount or border, 2 for a noise map's intensity, 3 for the vertex intensity, 4 for a tiling or colour; interpolate them like the rest. The members above hold their first values. Empty for a layer without a timeline, and in `exact`. |
| `mode`, `noise`, `noise1`, `noise2`, `glow` | `noise` family: `noise1`/`noise2` are `[scale, speed, x, y]`; the noise texture's red (at noise 1's scale, scrolled by `fract(t / 20 x speed)`) and green (noise 2's) move the main UV by `0.1 x (r x [x1, y1] + g x [x2, y2])`. `mode` `default`: texture x colour with vertex alpha; `add`: rgb x texture alpha; `glow`: texture.r x colour + texture.g x `glow`. |

Every texture an effect needs that sits in one of the client's shared FX bundles (`refs/fx/texture/flow.ab`, `mask.ab`... most noise and dissolve textures do) is exported into the folder like the others; `shared-bundles.json` (written by `scripts/shared_bundles.py`) says which bundle holds which CAB, and the sync fetches a bundle, md5-checked, only when a layer needs it.

## How the files are made

`scripts/sync.py` runs daily on GitHub Actions (`.github/workflows/sync.yml`) and can be started by hand with a `limit` or a list of skinIds.

1. It reads the EN `skin_table.json` from ArknightsAssets/ArknightsGamedata and the Global client's network config, version file and `hot_update_list.json`, then the zh_CN `skin_table.json` from Kengxxiao/ArknightsGameData and the CN client's (`ak-conf.hypergryph.com`, `ak.hycdn.cn`). Global comes first: CN only fills the skins Global's plan does not have. Each model is downloaded, and its shared shader and FX bundles read, from its own client, and `sync-failures.json` keeps the records of both clients' skins.
2. For each skin with a `dynIllustId` whose bundle (`arts/dynchars/<id>.ab`) the list carries, it skips the bundle if a folder for that md5 already exists. Otherwise it downloads the `.dat` from the client's asset CDN (GET only, one at a time, a 3 second pause between downloads) and checks its size and md5 against the list.
3. It decodes the bundle with UnityPy (plus the LZ4AK patch Arknights bundles need). When anything is to be built, the run first fetches the client's shared shader bundle `[uc]shaders.ab` (734 KB, size and md5 checked against the list, cached under `.cache/shared/`): layer materials name their shaders by reference into it. A layer whose texture sits in a shared FX bundle (`shared-bundles.json`) makes the run fetch that bundle the same way, once (`refs/fx/texture/flow.ab` 10 MB and `mask.ab` 3.6 MB are the usual ones). Without it nothing is built that run, and nothing is recorded as failed. File names come from the bundle, never from the id. The skeleton is the one the illustration prefab (`dyn/arts/dynchars/.../<id>.prefab` in the bundle's container) plays, and its SkeletonDataAsset links it to its atlas. Names alone are not enough: Kal'tsit's boc#6 bundle has an entrance skeleton with exactly the same name as the illustration. Names are used only for a bundle without that prefab, and then entrance (`_Start`, `_Start#N`) and portrait skeletons are left out. Particle textures and effect masks are always ignored. A skin with a `dynEntranceId` also takes the skeleton its entrance prefab (`dyn/arts/dyncharstart/.../<dynEntranceId>.prefab`) plays, never one picked by name, and the soundtrack under `.../dynentrance/<dynEntranceId>/`, exported by UnityPy and encoded as MP3 (lameenc). A missing entrance prefab fails the model.
4. Skeleton bytes are written unchanged. In the atlas only the page name lines change, to `page0.webp`, `page1.webp`, ... (the originals contain `#`, which breaks URLs). Each page is saved as premultiplied, lossless WebP at exactly the size the atlas was packed at. The client ships a page in one of two ways, and each needs different handling:
   - **One RGBA texture** (ASTC, e.g. Hoshiguma's Elite 2): straight alpha with colour under transparent texels. It is premultiplied here. A few ship already premultiplied (Goldenglow's summer#12); both measurements say so, and they are kept as shipped.
   - **An RGB texture plus a separate `[alpha]` mask** (ETC, e.g. Ch'en's Elite 2, Nian's Elite 2): the game's RGB is already premultiplied. The mask becomes the alpha channel and the colour is kept as shipped. Premultiplying again would darken every soft edge and glow.

   Before writing a page, the sync measures it: the colour under fully transparent texels, and how often colour exceeds alpha at alpha 16-63. Straight pages measure about 120-156 and 80-97%; masked pages measure under 0.1 and 5-14%. A page that doesn't match how it was shipped, or measures in between, fails the model instead of being written wrong. The result for each page is printed in the log, in `.cache/sync-report.json` (`pages`) and in the run summary.
5. `scripts/inspect-skeleton.mjs` reads the skeleton with the vendored Spine 3.8 runtime (`vendor/spine-core-3.8/`) to record the animations and bounds, then the layers (`scripts/layers.py`: meshes, materials, Mecanim clips through `scripts/entrance_camera.py`) are exported for its slot names and framed (`inspect-skeleton.mjs <folder> layers`). A skeleton without an `Idle` animation fails the model, because an entrance skeleton has only `Start`. Then the folder is moved into place and `manifest.json` is updated.

A folder whose model.json has an older `layersVersion` (or none) than the sync's (`LAYERS_VERSION` in `scripts/layers.py`) has its bundle downloaded and checked again, and only its layers are exported again into a copy of the folder, which then replaces it in one rename (the first run after a format change does this for every folder: about 434 MB of bundles, once).

A model that fails at any step is skipped and reported. The run still commits the models that worked, then shows as failed so someone looks. The manifest only ever names complete folders. Before anything is pushed the workflow runs the validator and the tests, because a push made with the workflow token does not start the Check workflow.

When a bundle downloads and verifies but can't be turned into a model, the sync records it in `sync-failures.json` (skinId, bundle md5, a fingerprint of the sync code, and the error) and commits the file. Later runs skip that bundle until the game ships a new one (a new md5) or the sync code changes, so a broken bundle isn't downloaded again every day. Skipped bundles show as warnings, and only a new failure marks the run as failed. To retry one by hand, run Sync with it in `only`, or with `retry_failed` for all of them. A download that fails or doesn't match the client list is never recorded, since it may work the next day.

`.github/workflows/check.yml` runs on every push and pull request. It installs the same Python packages as the sync, runs the Node and Python tests, and runs `npm run validate`, which checks every folder on disk (including old ones the manifest no longer names). It checks that each file exists with the recorded bytes and sha256, that the required fields are present, that the folder name matches the skinId and md5, that the atlas pages match the texture list and sizes, that the Spine runtime re-reads each skeleton to the recorded animations and bounds, and that `layers.json` is well formed for the folder's `layersVersion` (every index in range, every texture listed and drawn, a plain layer's texture among `textures`, every effect's shader complete and every map's texture listed, timelines in order), its textures match their records and sizes, its separator slots and bones exist in the skeleton and its `bounds` and `effectBounds` re-frame to the recorded ones. `shared-bundles.json` is checked to name `refs/fx/` bundles by CAB.

## Run it locally

Python 3.12 and Node.js 22. The scripts have no npm dependencies.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/sync.py --dry-run                      # what would be fetched, and how much
.venv/bin/python scripts/sync.py --only 'char_1044_hsgma2#2'    # quote ids: they contain # and @
.venv/bin/python scripts/sync.py --limit 10
.venv/bin/python scripts/sync.py --retry-failed                 # also retry bundles in sync-failures.json
.venv/bin/python scripts/sync.py --bundles ../bundles             # build from local unpacked bundles (<dir>/<slug>.ab), md5-checked
.venv/bin/python scripts/shared_bundles.py                      # rewrite shared-bundles.json (GETs every refs/fx/ bundle, ~55 MB)
npm run validate
npm test
.venv/bin/python -m unittest discover -s test -p 'test_*.py'
```

Run one sync at a time; the workflow's concurrency group makes sure two never overlap on GitHub.

## Using the files

Load `manifest.json` and the files through URLs pinned to a commit of this repository (`https://raw.githubusercontent.com/arkpedia/arkpedia-l2d-assets/<commit>/...`), never the moving `main`. Raw URLs send `Access-Control-Allow-Origin: *`. A JSON skeleton is fetched as text and a binary one as an ArrayBuffer; branch on `skeleton.format`. Render with the Spine 3.8 runtime and `premultipliedAlpha: true`.

## Size

The first full sync downloads 88 bundles, 434 MB (by the client list's `totalSize`), plus the 734 KB shared shader bundle. The repository will hold about 125 MB of skeletons and atlases plus 290 to 440 MB of lossless WebP pages, about 0.4 to 0.6 GB in all. The layers add about 131 MB (layersVersion 2, built from client `26-09-23-17-49-43_b9cc4a`: 2,248 layers, 1,756 of them with effect shaders, and 1,653 textures in 86 of the 88 models, 0 to 5.4 MB per model, 1.5 MB on average; layersVersion 1 held 61 MB). A run that builds anything may also fetch shared FX bundles its layers need (`refs/fx/texture/flow.ab` 10 MB and `mask.ab` 3.6 MB for most). Later runs only fetch bundles the game changes.

See [NOTICE.md](NOTICE.md) for ownership and provenance, and [vendor/spine-core-3.8/README.md](vendor/spine-core-3.8/README.md) for the Spine runtime's licence.
