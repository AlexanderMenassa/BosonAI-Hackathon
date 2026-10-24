# Phase 2B live setup

These steps use one Mac to run Hang On and a second phone or computer to test the receiver. Keep the Boson key and TURN secret on the Mac. Do not send either value in chat, a screenshot, a browser URL, or a GitHub issue.

## 1. Configure the Boson key on the Mac

1. Get a key from the Boson workspace's API Keys page.
2. In Terminal, prepare the ignored local configuration file:

   ```bash
   cd /Users/baiweizhong/BosonAI
   test -e .env || cp .env.example .env
   chmod 600 .env
   ```

3. Open `.env` in a text editor. Set `BOSON_API_KEY=` to your key. Leave `BOSON_REALTIME_URL` at its example value unless Boson gave you a different Realtime endpoint. Save the file. Do not print or paste its contents into Terminal output.
4. Install the project dependencies if needed: `uv pip install --python .venv/bin/python -r requirements.txt` (or use the project's existing environment installer). Check that `.venv/bin/python scripts/verify.py` passes. This command uses synthetic tests; it does not contact Boson.

## 2. Give both devices one HTTPS address

Tailscale Serve is one way to provide a trusted HTTPS URL without exposing the development server publicly. Install Tailscale on the Mac and second device, and sign in so the second device can reach the Mac through the same tailnet or an accepted device share. On the Mac:

```bash
tailscale serve --bg 8765
tailscale serve status
```

Copy the `https://<machine>.<tailnet>.ts.net` address shown by `tailscale serve status`. Put its origin, without a trailing slash, in `.env`:

```text
HANGON_PUBLIC_ORIGIN=https://<machine>.<tailnet>.ts.net
```

Start Hang On on the Mac in a separate Terminal:

```bash
cd /Users/baiweizhong/BosonAI
.venv/bin/python -m hangon.server --port 8765
```

Leave both commands running. Open the **same** Tailscale HTTPS address on both devices. The Mac server stays on localhost; Tailscale supplies HTTPS and the matching secure WebSocket connection. Do not use the localhost address on the second device.

## 3. Provide a WebRTC media route

The HTTPS address serves the page and signaling. Browser audio also needs a WebRTC route. If you have a TURN server configured for time-limited REST credentials (for example, coturn with a shared secret), add its URL and matching secret to the Mac's `.env`:

```text
HANGON_TURN_URL=turn:turn.example.org:3478?transport=udp
HANGON_TURN_SECRET=<your TURN REST shared secret>
HANGON_STUN_URL=stun:turn.example.org:3478
```

The example host and placeholder must be replaced. A fixed TURN username/password is not compatible with this configuration. Restart Hang On after editing `.env`. Set `HANGON_RELAY_ONLY=1` and restart for the explicit relay acceptance run. If you have no TURN server yet, try the call on the intended network, record whether audio connects, and leave the relay check pending; an HTTPS page alone does not prove media works.

## 4. Run the two-device check

1. On the caller device, open the shared URL and create a room. Give the displayed room code to the receiver.
2. On the second device, open the same URL, join as Receiver, and click **Ready for calls**. Allow microphone access when prompted. The receiver can open **Call lists** to add or remove test numbers.
3. Use headphones on both devices. On the caller, enter an unlisted test number such as `(416) 555-0104` and click **Call**. Allow microphone access. Speak; confirm Higgs audio plays and the caller's finalized speech appears.
4. When screening approves the call, confirm the receiver rings. Click **Accept**. Both people should hear each other, and Higgs should no longer be audible.
5. Add a different test number to the receiver's trust list and call from that number to check the direct route without Higgs. Add it to the block list too and confirm blocking takes priority on the next call. Test **End** from each device in separate calls, plus one spoken interruption while Higgs is talking.
6. Record the devices/browsers, network, whether TURN relay was used, and each observed result. Do not label offline or fake-server checks as live success.

If the page opens but microphone access fails, confirm the exact HTTPS URL and browser permission. If the page works but audio does not connect, inspect the on-screen connection status and TURN reachability first.
