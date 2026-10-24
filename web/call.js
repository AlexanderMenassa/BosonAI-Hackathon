"use strict";
const $ = id => document.getElementById(id);
let socket = null, token = null, role = null, room = null, snapshot = null;
let joining = false;
let localStream = null, micRequest = null, mediaEpoch = 0, peer = null, peerCall = null, offering = false;
let connectedAt = null, connectedCall = null, muted = false;
const active = () => snapshot && !["ended"].includes(snapshot.state) && snapshot.call_id;
const send = data => {
  if (!socket || socket.readyState !== WebSocket.OPEN || !token) return showError("Join a room first.");
  socket.send(JSON.stringify({...data, token}));
};
const showError = message => { $("error").textContent = message; };
const command = type => send({type, call_id:snapshot.call_id, event_id:crypto.randomUUID(), expected_revision:snapshot.revision});

function formatNumber(number) {
  const digits = (number || "").replace(/\D/g, "");
  return digits.length === 11 && digits[0] === "1" ? `+1 (${digits.slice(1,4)}) ${digits.slice(4,7)}-${digits.slice(7)}` : number || "—";
}

function renderList(id, entries, listName) {
  const target = $(id);
  target.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("li");
    empty.className = "empty-list";
    empty.textContent = "No numbers yet";
    target.append(empty);
  }
  for (const entry of entries) {
    const row = document.createElement("li"), name = document.createElement("span");
    const title = document.createElement("strong"), number = document.createElement("small");
    const remove = document.createElement("button");
    name.className = "contact-name";
    title.textContent = listName === "blocked" ? formatNumber(entry.number) : entry.label || formatNumber(entry.number);
    number.textContent = formatNumber(entry.number);
    remove.textContent = "Remove";
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${entry.number} from ${listName} list`);
    remove.onclick = () => send({type:"list.remove", list:listName, number:entry.number});
    name.append(title);
    if (listName !== "blocked") name.append(number);
    row.append(name, remove); target.append(row);
  }
}

function renderReceiverMessages(entries) {
  const target = $("receiver-messages");
  target.replaceChildren();
  if (!entries.length) { target.textContent = "No screening messages yet."; return; }
  for (const entry of [...entries].reverse()) {
    const card = document.createElement("article"), heading = document.createElement("strong");
    const number = document.createElement("p"), detail = document.createElement("p");
    card.className = `screening-note ${entry.kind}`;
    heading.textContent = entry.kind === "scam" ? "Likely scam" : "Unsure call";
    number.textContent = `Caller number: ${formatNumber(entry.number)}`;
    detail.textContent = entry.kind === "scam" ? `Why it was flagged: ${entry.detail}` : `Conversation summary: ${entry.detail}`;
    card.append(heading, number, detail);
    const status = document.createElement("small");
    status.textContent = entry.kind === "scam" ? "Number added to block list" : "Number not blocked · Decide whether to call back";
    card.append(status); target.append(card);
  }
}

function updateTimer() {
  $("phone-clock").textContent = new Intl.DateTimeFormat(undefined, {hour:"numeric", minute:"2-digit"}).format(new Date());
  const seconds = connectedAt ? Math.floor((Date.now() - connectedAt) / 1000) : 0;
  $("call-timer").textContent = connectedAt ? `${String(Math.floor(seconds / 60)).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}` : "";
}

function render() {
  const state = snapshot?.state || "idle", inCall = !!active(), hasCall = !!snapshot?.call_id;
  const currentScreeningMessage = role === "receiver" && state === "ended" ?
    [...(snapshot?.receiver_messages || [])].reverse().find(entry => entry.call_id === snapshot.call_id) : null;
  const messageReason = [...(snapshot?.events || [])].reverse().find(e => e.type === "call.message_requested")?.detail;
  const messageDetail = ({screening_unavailable:"Screening audio failed",
    media_unavailable:"Audio connection failed", receiver_unavailable:"The receiver is unavailable",
    receiver_declined:"The receiver declined", ring_timeout:"The receiver did not answer"})[messageReason] ||
    "The call could not continue";
  const screeningIssue = ({screening_key_missing:"Higgs API key is missing on the server",
    screening_auth_failed:"Higgs rejected the server key",
    screening_credits_unavailable:"Higgs credits are unavailable",
    screening_configuration_rejected:"Higgs rejected the audio setup",
    screening_provider_error:"Higgs reported a session error",
    screening_service_busy:"Higgs is busy; retry the call",
    screening_timeout:"Higgs did not respond in time",
    screening_connection_closed:"Higgs closed the connection",
    screening_connection_failed:"Could not connect to Higgs",
    screening_session_ended:"Higgs ended the session",
    screening_audio_overflow:"Audio could not keep up with the call"})[snapshot?.screening_issue];
  if (state === "connected" && (connectedCall !== snapshot.call_id || !connectedAt)) {
    connectedCall = snapshot.call_id; connectedAt = Date.now();
  } else if (state !== "connected") { connectedAt = null; connectedCall = null; }
  $("phone-clock").textContent = new Intl.DateTimeFormat(undefined, {hour:"numeric", minute:"2-digit"}).format(new Date());
  document.querySelector(".phone").dataset.callState = state;
  $("room").textContent = room || "—";
  $("myrole").textContent = role ? role[0].toUpperCase() + role.slice(1) : "—";
  $("presence").textContent = snapshot ? (snapshot.receiver_busy ? "Call in progress" : snapshot.receiver_ready ? "Receiver ready" : snapshot.receiver_online ? "Receiver online" : "Receiver offline") : "Not connected";
  $("route").textContent = snapshot?.route || "—";
  $("state").textContent = state.replaceAll("_", " ");
  const phases = {idle:"READY WHEN YOU ARE", screening:"SCREENING CALL", ringing:role === "receiver" ? "INCOMING CALL" : "RINGING RECEIVER", connecting:"CONNECTING AUDIO", connected:"ON A CALL", message_pending:"MESSAGE REQUESTED", ended:"CALL ENDED"};
  $("call-phase").textContent = !role ? "SET UP A ROOM TO BEGIN" : !hasCall ? "READY WHEN YOU ARE" : phases[state] || state.toUpperCase();
  $("display-number").textContent = snapshot?.caller_number ? formatNumber(snapshot.caller_number) : role === "receiver" ? "No incoming call" : "Hang On";
  $("call-detail").textContent = !role ? "A private call space for two browsers" : !hasCall ? role === "receiver" ? "Be ready to receive calls" : "Enter a test number to call" : state === "screening" ? "Your call is being screened" : state === "ringing" ? role === "receiver" ? "Tap Accept to connect" : "Waiting for the receiver" : state === "connected" ? "Connected through browser audio" : state === "message_pending" ? screeningIssue || messageDetail : state === "ended" ? currentScreeningMessage?.kind === "scam" ? `Likely scam from ${formatNumber(currentScreeningMessage.number)}. ${currentScreeningMessage.detail}` : currentScreeningMessage ? `Screening message from ${formatNumber(currentScreeningMessage.number)}: ${currentScreeningMessage.detail}` : "The call has finished" : "Getting ready";
  if (snapshot?.state === "connected") $("remote-audio").muted = false;
  $("summary").textContent = snapshot?.summary ? `Screening summary: ${snapshot.summary}` : "";
  $("assistant-caption").textContent = snapshot?.assistant_caption ?
    `Assistant caption (generated; may not all have played): ${snapshot.assistant_caption}` : "";
  $("audio-status").textContent = `Audio ${snapshot?.audio || "idle"}`;
  $("caller-controls").hidden = role !== "caller" || inCall;
  $("receiver-controls").hidden = role !== "receiver";
  $("contacts-module").hidden = role === "caller";
  $("receiver-inbox").hidden = role !== "receiver";
  $("add-number").disabled = role !== "receiver";
  $("start").disabled = role !== "caller" || !snapshot || inCall;
  $("line-controls").hidden = !snapshot?.synthetic_mode;
  $("send-line").disabled = role !== "caller" || snapshot?.state !== "screening" || !snapshot?.synthetic_mode;
  $("send-message").disabled = role !== "caller" || snapshot?.state !== "message_pending";
  $("end").hidden = !inCall || (role === "receiver" && !["ringing","connecting","connected"].includes(state));
  $("call-tools").hidden = !inCall || ["message_pending"].includes(state);
  $("mute").disabled = !localStream;
  $("message-card").hidden = role !== "caller" || state !== "message_pending";
  $("accept").disabled = role !== "receiver" || snapshot?.state !== "ringing";
  $("decline").disabled = $("accept").disabled;
  document.querySelector(".incoming-actions").hidden = role !== "receiver" || state !== "ringing";
  document.querySelector(".ready-row").hidden = role !== "receiver" || ["ringing","connecting","connected"].includes(state);
  $("ready").disabled = role !== "receiver" || !snapshot || snapshot.receiver_ready;
  $("not-ready").disabled = role !== "receiver" || !snapshot?.receiver_ready;
  $("events").textContent = (snapshot?.events || []).map(e => `${e.revision}. ${e.type.replaceAll("."," ")} ${e.detail || ""}`).join("\n") || "No activity yet";
  renderList("trusted-list", snapshot?.trusted_numbers || [], "trusted");
  renderList("blocked-list", snapshot?.blocked_numbers || [], "blocked");
  renderReceiverMessages(snapshot?.receiver_messages || []);
  updateTimer();
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
  muted = false;
  $("mute").setAttribute("aria-pressed", "false");
  $("mute").lastChild.textContent = " Mute";
  if (peer) { peer.close(); peer = null; }
  if (localStream) { localStream.getTracks().forEach(track => track.stop()); localStream = null; }
  $("remote-audio").srcObject = null;
  $("remote-audio").muted = false;
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
    audio.muted = !(role === "caller" && snapshot?.state === "screening");
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
      if (role === "receiver") { $("room-module").open = false; $("contacts-module").open = true; }
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
$("copy-room").onclick = async () => {
  if (!room) return showError("Create or join a room first.");
  try { await navigator.clipboard.writeText(room); showError("Room code copied."); }
  catch { showError("Select and copy the room code above."); }
};
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
  try {
    const number = $("dial-number").value.trim();
    if (!/^\+?[\d() .-]{10,32}$/.test(number) || ![10,11].includes(number.replace(/\D/g, "").length))
      throw new Error("Enter a 10-digit test phone number first.");
    await microphone();
    send({type:"call.start", number, request_id:crypto.randomUUID()});
  }
  catch (error) { showError(error.message); $("start").disabled = false; }
};
$("add-number").onclick = () => {
  if (role !== "receiver") return showError("Join as the receiver to manage call lists.");
  const number = $("list-number").value.trim();
  if (!number) return showError("Enter a phone number to add.");
  send({type:"list.add", list:$("list-kind").value, number, label:$("list-label").value.trim()});
  $("list-number").value = "";
  $("list-label").value = "";
};
$("send-line").onclick = () => send({type:"call.line", call_id:snapshot.call_id, text:$("line").value});
$("send-message").onclick = () => send({type:"call.message", call_id:snapshot.call_id, text:$("message").value,
                                         event_id:crypto.randomUUID(), expected_revision:snapshot.revision});
$("accept").onclick = () => command("call.accept");
$("decline").onclick = () => command("call.decline");
$("end").onclick = () => command("call.end");
$("mute").onclick = () => {
  if (!localStream) return;
  muted = !muted;
  localStream.getAudioTracks().forEach(track => { track.enabled = !muted; });
  $("mute").setAttribute("aria-pressed", String(muted));
  $("mute").lastChild.textContent = muted ? " Unmute" : " Mute";
};
$("play-audio").onclick = async () => {
  try { $("remote-audio").muted = false; await $("remote-audio").play(); $("play-audio").hidden = true; showError(""); }
  catch { showError("Sound is still blocked. Check browser playback settings."); }
};
setInterval(updateTimer, 1000);
render();
