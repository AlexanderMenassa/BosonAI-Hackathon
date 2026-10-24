"""Exercise the browser's audio element across consecutive screened calls."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="Node audio check hangs on CI")
def test_screening_audio_is_unmuted_after_previous_call():
    if not shutil.which("node"):
        pytest.skip("Node is needed for the browser audio check")
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    muted: false, hidden: false, style: {}, dataset: {}, lastChild: {},
    replaceChildren() {}, append() {}, setAttribute() {},
    async play() {},
  });
  return elements.get(id);
};
let pc;
class Peer {
  constructor() { pc = this; this.localDescription = {sdp: 'offer'}; }
  addTrack() {}
  async createOffer() { return {}; }
  async setLocalDescription() {}
  close() {}
}
const context = vm.createContext({
  document: {getElementById: element, querySelector: element,
    createElement: () => ({append() {}, setAttribute() {}})},
  window: {}, setInterval() {}, setTimeout() {},
  RTCPeerConnection: Peer, WebSocket: {OPEN: 1},
  MediaStream: class {}, console,
});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
vm.runInContext(`
  role = 'caller'; token = 'test';
  socket = {readyState: 1, send() {}};
  localStream = {getTracks: () => [{stop() {}}]};
  snapshot = {state: 'screening', ice_servers: [], relay_only: false};
`, context);
(async () => {
  await vm.runInContext(`offerMedia('call-1')`, context);
  await pc.ontrack({streams: [{}]});
  assert.equal(element('remote-audio').muted, false);
  element('remote-audio').muted = true;
  vm.runInContext('stopMedia()', context);
  assert.equal(element('remote-audio').muted, false);
  vm.runInContext(`
    localStream = {getTracks: () => [{stop() {}}]};
    snapshot = {state: 'screening', ice_servers: [], relay_only: false};
  `, context);
  await vm.runInContext(`offerMedia('call-2')`, context);
  await pc.ontrack({streams: [{}]});
  assert.equal(element('remote-audio').muted, false);
  vm.runInContext(`
    snapshot = {state: 'message_pending', call_id: 'call-2',
      events: [{type: 'call.message_requested', detail: 'screening_unavailable'}]};
    render();
  `, context);
  assert.equal(element('call-detail').textContent, 'Screening audio failed');
  vm.runInContext(`
    snapshot.screening_issue = 'screening_auth_failed';
    render();
  `, context);
  assert.equal(element('call-detail').textContent, 'Higgs rejected the server key');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    source = Path(__file__).resolve().parents[1] / "web" / "call.js"
    subprocess.run(["node", "-e", script, str(source)], check=True, timeout=10)
