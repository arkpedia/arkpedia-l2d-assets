#!/usr/bin/env python3
"""Writes shared-bundles.json: which of the client's shared FX bundles holds which CAB.

Effect materials in the illustration prefabs take their noise, dissolve, ramp and some main textures
from the client's shared FX texture bundles (refs/fx/texture/flow.ab, mask.ab, ...). A material names a
texture by CAB name and path id; the client's index that maps a CAB to its bundle is not readable, so
this reads the CAB name out of every refs/fx/ bundle that holds textures and records it. scripts/sync.py
then fetches only the bundles a model's layers need. Run it by hand when a sync leaves layers out with
"texture in CAB-..." for a CAB the file does not list (a new shared bundle):

  python scripts/shared_bundles.py                 # GET each refs/fx/ bundle (md5-checked, cached in .cache/shared/)
  python scripts/shared_bundles.py --bundles DIR   # or read them from DIR (<download name>.ab or .dat)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import l2d  # noqa: E402
import layers  # noqa: E402
import sync  # noqa: E402

PREFIX = 'refs/fx/'


def table_of(bundles: dict) -> dict:
    """shared-bundles.json from {bundle name: (CAB name, texture count)}: bundles without textures are left out."""
    return {'schemaVersion': 1, 'bundles': {cab: name for name, (cab, textures) in sorted(bundles.items(), key=lambda kv: kv[1][0]) if textures}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bundles', type=Path, default=None, help='read the bundles from this folder instead of downloading them')
    parser.add_argument('--pause', type=float, default=3.0, help='seconds between downloads')
    args = parser.parse_args(argv)
    asset_base, res_version, hot_update_list = sync.client_list()
    report = {'downloadedBytes': 0}
    names = sorted(info['name'] for info in hot_update_list.get('abInfos', []) if str(info.get('name', '')).startswith(PREFIX))
    unitypy = l2d._patch_unitypy()
    shared = sync.load_shared_textures(asset_base, res_version, hot_update_list, args.bundles, report, args.pause)
    found = {}
    for index, name in enumerate(names):
        data = shared.fetch(name)
        if data is None:
            continue
        env = unitypy.load(data)
        textures = sum(1 for o in env.objects if o.type.name == 'Texture2D')
        found[name] = (layers.cab_name(env), textures)
        sync.log(f'[{index + 1}/{len(names)}] {name}: {found[name][0]}, {textures} textures')
    table = table_of(found)
    sync.write_json(sync.ROOT / sync.SHARED_BUNDLES_FILE, table)
    sync.log(f'Wrote {sync.SHARED_BUNDLES_FILE}: {len(table["bundles"])} bundles ({res_version}), downloaded {report["downloadedBytes"] / 1e6:.1f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
