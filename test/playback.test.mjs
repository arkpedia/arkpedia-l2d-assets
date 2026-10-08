import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {playbackShape} from '../scripts/playback.mjs';
for(const path of ['models/char_4202_haruka_iteration_6/f869b36a807c/model.json','models/char_1041_angel2_iteration_6_sp_dyn/773ec69ee2c6/model.json','models/char_003_kalts_sale_14_sp_dyn/ad673b10f4d4/model.json']) {
 test(`authored playback references that exact model: ${path}`,()=>{
  const m=JSON.parse(readFileSync(path,'utf8'));
  playbackShape(m.playback,m.animations);
  assert.throws(()=>playbackShape(m.playback,{Idle:1}));
  assert.equal(m.playbackVersion,1);
 });
}
