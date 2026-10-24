"use strict";
const $ = id => document.getElementById(id);
let socket = null, token = null, role = null, room = null, snapshot = null;
let joining = false;
let localStream = null, micRequest = null, mediaEpoch = 0, peer = null, peerCall = null, offering = false;
const active = () => snapshot && !["ended"].includes(snapshot.state) && snapshot.call_id;
const send = data => {
  if (!socket || socket.readyState !== WebSocket.OPEN || !token) return showError("Join a room first.");
  socket.send(JSON.stringify({...data, token}));
};
const showError = message => { $("error").textContent = message; };
const command = type => send({type, call_id:snapshot.call_id, event_id:crypto.randomUUID(), expected_revision:snapshot.revision});

function render() {
  $("room").textContent = room || "—";
  $("myrole").textContent = role || "—";
  $("presence").textContent = snapshot ? (snapshot.receiver_busy ? "busy" : snapshot.receiver_ready ? "ready" : snapshot.receiver_online ? "online, not ready" : "offline") : "offline";
  $("route").textContent = snapshot?.route || "—";
  $("state").textContent = snapshot?.state || "idle";
  if (snapshot?.state === "connected") $("remote-audio").muted = false;
  $("summary").textContent = snapshot?.summary ? `Screening summary: ${snapshot.summary}` : "";
  $("assistant-caption").textContent = snapshot?.assistant_caption ?
    `Assistant caption (generated; may not all have played): ${snapshot.assistant_caption}` : "";
  $("audio-status").textContent = snapshot?.audio || "idle";
  $("caller-controls").hidden = role !== "caller";
  $("receiver-controls").hidden = role !== "receiver";
  $("start").disabled = role !== "caller" || !snapshot || !!active();
  $("line-controls").hidden = !snapshot?.synthetic_mode;
  $("send-line").disabled = role !== "caller" || snapshot?.state !== "screening" || !snapshot?.synthetic_mode;
  $("send-message").disabled = role !== "caller" || snapshot?.state !== "message_pending";
  $("end").disabled = !active() || (role === "receiver" && !["ringing","connecting","connected"].includes(snapshot.state));
  $("accept").disabled = role !== "receiver" || snapshot?.state !== "ringing";
  $("decline").disabled = $("accept").disabled;
  $("ready").disabled = role !== "receiver" || !snapshot || snapshot.receiver_ready;
  $("not-ready").disabled = role !== "receiver" || !snapshot?.receiver_ready;
  $("events").textContent = (snapshot?.events || []).map(e => `${e.revision}. ${e.type} ${e.detail || ""}`).join("\n");
}

async function microphone() {
  if (localStream) return localStream;
  if (micRequest) return micRequest;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
    throw new Error("Microphone needs HTTPS or localhost. Open the shared secure URL.");
  const epoch = mediaEpoch;
  try {
    micRequest = navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,
      noiseSuppression:true, autoGainControl:true}, video:false});
    localStream = await micRequest;
    if (epoch !== mediaEpoch) {
      localStream.getTracks().forEach(track => track.stop());
      localStream = null;
      throw new Error("Call ended while microphone permission was pending.");
    }
    return localStream;
  } catch (error) {
    if (error.message === "Call ended while microphone permission was pending.") throw error;
    throw new Error(`Microphone unavailable (${error.name}). Check permission and device, then retry.`);
  } finally {
    micRequest = null;
  }
}

function stopMedia() {
  mediaEpoch++;
  peerCall = null;
  offering = false;
  if (peer) { peer.close(); peer = null; }
  if (localStream) { localStream.getTracks().forEach(track => track.stop()); localStream = null; }
  $("remote-audio").srcObject = null;
  $("audio-status").textContent = "stopped";
}

async function offerMedia(callId) {
  if (offering || peerCall === callId || !localStream || !socket || socket.readyState !== WebSocket.OPEN) return;
  offering = true;
  const pc = new RTCPeerConnection({iceServers:snapshot.ice_servers || [],
    iceTransportPolicy:snapshot.relay_only ? "relay" : "all"});
  peer = pc;
  peerCall = callId;
  localStream.getTracks().forEach(track => pc.addTrack(track, localStream));
  pc.ontrack = async event => {
    if (peer !== pc || peerCall !== callId) return;
    const audio = $("remote-audio");
    audio.srcObject = event.streams[0] || new MediaStream([event.track]);
    try { await audio.play(); $("play-audio").hidden = true; }
    catch { $("play-audio").hidden = false; showError("Tap Enable sound to hear the call."); }
  };
  pc.onconnectionstatechange = () => {
    if (peer !== pc) return;
    $("audio-status").textContent = pc.connectionState;
    if (["failed","disconnected"].includes(pc.connectionState))
      showError("Audio connection failed. End the call and retry on a reachable network.");
  };
  try {
    await pc.setLocalDescription(await pc.createOffer());
    if (peer !== pc) return;
    send({type:"media.offer", call_id:callId, sdp:pc.localDescription.sdp});
  } catch (error) {
    if (peer === pc) { stopMedia(); showError(`Could not start audio (${error.message}). Retry the call.`); }
  } finally { offering = false; }
}

function syncMedia() {
  if (!snapshot?.call_id) return;
  if (["ended","message_pending"].includes(snapshot.state)) {
    if (peer || localStream) stopMedia();
    return;
  }
  if (role === "caller" && ["screening","ringing","connecting","connected"].includes(snapshot.state))
    offerMedia(snapshot.call_id);
  if (role === "receiver" && ["ringing","connecting","connected"].includes(snapshot.state)) {
    const callId = snapshot.call_id;
    microphone().then(() => {
      if (snapshot?.call_id === callId && ["ringing","connecting","connected"].includes(snapshot.state))
        offerMedia(callId);
    }).catch(error => showError(error.message));
  }
}

function connect(first) {
  if (joining || (socket && socket.readyState === WebSocket.OPEN)) return;
  joining = true;
  showError("");
  socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  socket.onopen = () => socket.send(JSON.stringify(first));
  socket.onmessage = event => {
    const data = JSON.parse(event.data);
    if (data.type === "error") { showError(data.message); return; }
    if (data.type === "room.joined") {
      joining = false; room = data.room; role = data.role; token = data.token;
      $("code").value = room;
      sessionStorage.setItem(`hangon:${room}:${role}`, token);
    } else if (data.type === "room.state") { snapshot = data; syncMedia(); }
    else if (data.type === "media.answer") {
      if (peer && peerCall === data.call_id)
        peer.setRemoteDescription({type:"answer", sdp:data.sdp}).catch(error =>
          showError(`Audio answer failed (${error.message}). End and retry.`));
    } else if (data.type === "media.clear" && peerCall === data.call_id) {
      const audio = $("remote-audio"), stream = audio.srcObject;
      audio.muted = true;
      audio.srcObject = null;
      if (stream) { audio.srcObject = stream; audio.play().catch(() => { $("play-audio").hidden = false; }); }
      if (data.reason === "interruption") setTimeout(() => {
        if (peerCall === data.call_id && snapshot?.state === "screening") audio.muted = false;
      }, 200);
    }
    render();
  };
  socket.onerror = () => showError("Connection failed. Retry joining the room.");
  socket.onclose = () => { joining = false; socket = null; snapshot = null; stopMedia(); render(); showError("Disconnected. Rejoin the room to continue."); };
}

$("create").onclick = () => connect({type:"room.create"});
$("join").onclick = () => {
  const code = $("code").value.trim(), selectedRole = $("role").value;
  if (!code) return showError("Enter an invite code.");
  connect({type:"room.join", room:code, role:selectedRole,
           token:sessionStorage.getItem(`hangon:${code}:${selectedRole}`)});
};
$("ready").onclick = async () => {
  $("ready").disabled = true;
  try {
    await microphone();
    localStream.getTracks().forEach(track => track.stop());
    localStream = null;
    send({type:"receiver.ready", ready:true});
  }
  catch (error) { showError(error.message); $("ready").disabled = false; }
};
$("not-ready").onclick = () => { send({type:"receiver.ready", ready:false}); stopMedia(); };
$("start").onclick = async () => {
  $("start").disabled = true;
  try { await microphone(); send({type:"call.start", profile:$("profile").value, request_id:crypto.randomUUID()}); }
  catch (error) { showError(error.message); $("start").disabled = false; }
};
$("send-line").onclick = () => send({type:"call.line", call_id:snapshot.call_id, text:$("line").value});
$("send-message").onclick = () => send({type:"call.message", call_id:snapshot.call_id, text:$("message").value,
                                         event_id:crypto.randomUUID(), expected_revision:snapshot.revision});
$("accept").onclick = () => command("call.accept");
$("decline").onclick = () => command("call.decline");
$("end").onclick = () => command("call.end");
$("play-audio").onclick = async () => {
  try { await $("remote-audio").play(); $("play-audio").hidden = true; showError(""); }
  catch { showError("Sound is still blocked. Check browser playback settings."); }
};
render();
