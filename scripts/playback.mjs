/** Validate the controller data independently of the frontend reader. */
export function playbackShape(p, animations) {
  if (p == null) return;
  const fail = () => { throw new Error('Invalid animation playback metadata'); };
  const name = n => typeof n === 'string' && Object.hasOwn(animations, n);
  const time = t => typeof t === 'number' && Number.isFinite(t) && t >= 0;
  if (!p || !Array.isArray(p.extraTracks) || !Array.isArray(p.voiceActions)) fail();
  const tracks = new Set(), ids = new Set();
  for (const x of p.extraTracks) {
    if (!x || !Number.isInteger(x.track) || x.track < 2 || x.track > 16 || tracks.has(x.track) || !name(x.name)) fail();
    tracks.add(x.track);
  }
  for (const a of p.voiceActions) {
    if (!a || !/^CN_\d+$/.test(a.id) || ids.has(a.id) || !Array.isArray(a.body) || !a.body.length || !Array.isArray(a.mouth)) fail();
    ids.add(a.id);
    for (const kind of ['body', 'mouth']) {
      let previous = -1;
      for (const x of a[kind]) {
        if (!x || !name(x.name) || !time(x.time) || x.time < previous || (kind === 'body' && !time(x.mix))) fail();
        previous = x.time;
      }
    }
  }
}
