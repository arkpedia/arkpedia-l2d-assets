# Original event entrance sources

This archive preserves the game's event UI animations for future Arkpedia event-page
entrances. These are **source recoveries, not browser-playable transitions**. The
game's scene layout, shader behaviour, animation bindings and script-driven changes
still need to be reproduced and checked against the game before enabling an entry.
Do not substitute a candidate clip's name for that verification.

`manifest.json` lists recovered event packs. `activity-catalogue.json` supplies
Global/CN event titles, activity types, dates and rerun flags from pinned game data.
Keep regional variants separate: CN text/art is not a verified Global replacement.
Original runs and reruns also remain separate, even when they share entrance art.

## Pack layout

`events/<en|cn>/<activityId>/<bundle-md5-prefix>/` contains:

- `asset.json`: source URLs, exact client resource version, original bundle MD5,
  archive/bundle SHA-256, animation inventory, external CAB dependencies, decoder
  errors and hashes for every delivered file. `browserPlayable` stays `false`.
- `bundle.dat`: the unchanged ZIP fetched from the game's CDN. Its single Unity
  bundle is verified against the manifest's byte sizes and MD5. It retains the
  original data even where a decoder cannot interpret an object.
- `animations.json.gz`: gzip-compressed original AnimationClip type trees, including
  curve keyframes, tangents, bindings and events. Decompress before parsing JSON.
  **Never execute recovered game event callbacks.**
- `scene.json.gz`: the complete readable object type trees from that event bundle,
  including prefab containers, transforms, sprites, materials, particles, animation
  components and external-file tables. Decompress with gzip before parsing JSON.
- `textures/*.webp`: lossless decoded texture channels, with their original names,
  object IDs and dimensions in `asset.json`. Texture payloads in the scene point to
  these files; the untouched archive retains the compressed originals.
- `audio/*`, when present: decoded AudioClip samples, with names in their scene
  objects' `$decodedAudio` fields. The original compressed audio stays in the archive.

Unity references use a per-serialized-file `m_FileID` and signed int64 `m_PathID`.
Path IDs are **strings** to prevent JavaScript's number rounding. The scene's
`externals[file][m_FileID - 1]` identifies an external file; zero means the same file.
Binary type-tree fields use `{"$bytesBase64":"..."}`; infinite/NaN values use
`{"$float":"inf"}`, `"-inf"` or `"nan"` (infinite curve slopes can mean steps).
Do not change those tagged values to zero or linearly interpolate stepped curves.

Textures retain the decoded channels. Some Android UI assets use a separate alpha
texture; some material shaders use vertex colour channels as wipe parameters.
Do not assume every texture is straight-alpha standalone artwork or multiply all
UI vertex colours as ordinary tints. Preserve sprite UVs, atlas offsets, masks and
material properties when implementing a renderer.

`shared-manifest.json` lists version-matched UI shader, shader, event sound-effect and common activity
packs recovered under `shared/<bundle-md5>/`. Match these to event external-file
tables by CAB name, not by filename or the newest client version. The shared archive
does not imply all dependencies have been resolved: external script assemblies,
fonts, other atlas bundles and game code can still be absent. Decode errors and
unresolved references must remain visible during integration.

`dependencies.json` links external CAB names to shared packs only when their bundle
MD5 also appears in that event's original manifest, and leaves missing dependencies
explicit. A newer shader bundle with the same CAB name is not a substitute.
`audio-catalogue.json` indexes decoded sound
effects by their original AudioClip names. `animation-cues.json` collects authored
event times and callback data, including entrance sound cues. Associations between
those callback names and particular sounds still need verification; the archive
does not invent mappings or executable callbacks.

## Coverage and recovery

`coverage.json` records each scanned activity bundle as `recovered`,
`no-candidate-clip`, `inspection-failed` or `unavailable`. A candidate is a non-loop
AnimationClip whose name suggests an entrance/open/transition. Some candidates are
auxiliary UI entrances (shops, missions or sign-in pages), so the inventory is not a
count of verified event transitions. No matching name does not prove the original
game lacked an entrance; it may be script-driven or live in another bundle.

`recovery-sources.json` freezes reviewed manifest URLs from official CDN lists and
commit-pinned datamine snapshots, back to November 2023. It is not an exhaustive
history of every game release. Older game versions and gaps in those archives may
contain further recoveries. Upstream TEST manifests are not treated as releases.

From this repository, after installing `requirements.txt`:

```sh
python scripts/event_transitions.py recover --workers 3
python scripts/event_transitions.py shared --workers 3
python scripts/event_transitions.py validate
```

`recover --only act42side` limits a run without removing earlier packs or coverage.
The ignored `.cache/event-transitions/` holds fetched manifests/bundles and logs.
Existing immutable folders are preserved. Add reviewed archived manifest URLs to
`recovery-sources.json` to expand the search. The recovery is manual; it does not add
a scheduled job. `npm run validate:events` validates archive hashes, original bundle
MD5/SHA-256, exported files, clip IDs and decoded texture dimensions.

## Planned site behaviour

The requested reader flow is: any event link → quick fade to black → original
entrance at full-screen size → final menu artwork shrinks into the event page's
banner bounds → crossfade to the page's existing event artwork. Full-screen playback
is an explicitly approved exception to the site's usual content frame. Preserve
ordinary link and browser history behaviour, skip unavailable/unverified assets,
and respect reduced-motion preferences. Use the original accompanying sound effects
at their authored cue times, then start the event music on cue. Both must honour
the reader's background-play setting; the entrance must not bypass a disabled audio
preference. Reinterpret only explicitly supported animation events, never execute
arbitrary callbacks recovered from the game. The site integration is separate from
this source archive.
