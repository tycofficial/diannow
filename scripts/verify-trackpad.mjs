import assert from 'node:assert/strict';
import {TrackpadGestures} from '../web/gestures.mjs';
const events=[];let buzz=0;const pad=new TrackpadGestures({emit:e=>events.push(e),haptic:()=>buzz++,speed:()=>2,clock:()=>0});const e=(x,y,id=1)=>({clientX:x,clientY:y,pointerId:id});
pad.down(e(20,20));pad.move(e(30,40));pad.up(e(30,40));assert.deepEqual(events,[{type:'relative',dx:20,dy:40}]);
events.length=0;pad.down(e(20,20));pad.up(e(20,20));assert.deepEqual(events.map(e=>[e.button,e.down]),[[0,true],[0,false]]);assert.equal(buzz,1);
events.length=0;pad.down(e(20,20));pad.down(e(40,20,2));pad.up(e(20,20));pad.up(e(40,20,2));assert(events.every(e=>e.button===2));
events.length=0;pad.down(e(20,20));pad.down(e(40,20,2));pad.move(e(40,70,2));pad.up(e(20,20));pad.up(e(40,70,2));assert(events.some(e=>e.type==='wheel'));assert(!events.some(e=>e.type==='button'));
events.length=0;assert(pad.toggleDrag());pad.down(e(20,20));pad.move(e(50,60));pad.cancel();assert.equal(events[0].down,true);assert.equal(events.at(-1).down,false);
console.log('Trackpad relative motion, click, two-finger right click/scroll, speed, drag lock and feedback passed.');
