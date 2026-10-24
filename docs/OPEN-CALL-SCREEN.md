# Open the call screen on two devices

Use one Mac to run Hang On and one phone or computer as the second device. Both devices open the same secure URL; the Mac uses the caller screen and the second device uses the receiver screen.

## Before you start

- Install and sign in to Tailscale on the Mac and second device. The second device must be able to reach the Mac through the same tailnet or an accepted device share.
- Configure the local `.env` as described in [Phase 2B live setup](PHASE-2B-SETUP.md). Keep the Boson key and any TURN secret on the Mac.
- For live screening, confirm `BOSON_API_KEY` is set locally. For audio across restrictive networks, configure a reachable TURN service as described in that setup guide.

## Start the shared call page

1. On the Mac, open Terminal and go to the project folder:

   ```bash
   cd /Users/baiweizhong/BosonAI
   ```

2. Start the secure Tailscale address if it is not already running:

   ```bash
   tailscale serve --bg 8765
   tailscale serve status
   ```

3. Copy the HTTPS address shown by `tailscale serve status`. It will look like `https://<machine>.<tailnet>.ts.net`. The exact address is specific to your Mac.
4. In `.env`, set `HANGON_PUBLIC_ORIGIN` to that address, without a trailing slash. Restart the app if you changed this setting.
5. Start Hang On in Terminal:

   ```bash
   .venv/bin/python -m hangon.server --port 8765
   ```

   Leave this process running while you use the call screens.

## Open the caller screen

1. On the Mac, open the shared HTTPS address in a browser.
2. In **Private room**, select **Caller** if needed, then choose **Create room as caller**.
3. Copy the room code shown in the page and send it to the receiver using your usual method.
4. Enter a simulated phone number, such as `(416) 555-0104`. The number is used only to test routing in this app; it does not place a carrier call.

## Open the receiver screen

1. On the second device, open the exact same HTTPS address.
2. In **Private room**, enter the caller's room code, select **Receiver**, and choose **Join room**.
3. In **Call lists**, add or remove simulated numbers as needed. A number on the block list is rejected; a number only on the trust list rings directly; any other number goes through screening. If a number is on both lists, the block list wins. List changes affect the next call.
4. Tap **Ready for calls** and allow microphone access. The screen should show the receiver as ready.

## Place a call

1. Put headphones on both devices to reduce echo.
2. On the caller screen, enter the simulated number and tap **Call**. Allow microphone access.
3. For an unlisted number, speak to Higgs while the call is screened. If screening approves the call, the receiver screen rings.
4. On the receiver, tap **Accept**. Wait for the screen to show a connected call, then speak normally. Tap **End call** on either device to hang up.

If the caller's number is trusted, the receiver rings without Higgs screening. If it is blocked, the call ends without ringing the receiver. Keep both browser tabs and the Mac server open for the full call.

## If the screen or call does not work

- If the second device cannot open the page, verify it has Tailscale access and that you used the exact HTTPS address, not `localhost`.
- If microphone access is unavailable, verify the page uses HTTPS and allow microphone access in the browser.
- If the page opens but audio does not connect, check the displayed call state and audio status, then confirm the TURN address is reachable from the devices.
