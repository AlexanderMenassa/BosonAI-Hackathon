# Hang On: submission sheet (copy each field into the form)

Anything in `[[double brackets]]` still needs your input. Find them all with: `grep -n "\[\[" SUBMISSION.md README.md`

---

## 01 Project information

**Project name:** Hang On

**Track:** Voice interaction

**One-sentence description (showcase):**
Hang On is an AI call screener that answers unknown calls as an elderly relative, draws scammers into revealing themselves, ends confirmed scams with evidence for the family, and puts ordinary callers through.

**Write-up (183 words):**

**Problem.** Scammers pose as banks, tax agencies and grandchildren, and they target older people who must judge in seconds whether a stranger is real. Call blockers only look at numbers; they cannot hear what is said.

**Who it's for.** Older adults who receive unknown calls, and the family who worry about them.

**How it works.** Every unknown call reaches a gateway first. Blocked numbers are rejected, trusted numbers ring straight through, and everyone else is answered by Walter, an 88-year-old persona voiced live by Higgs Realtime. Scammers relax with an elderly person and make their real pitch, which is exactly what we need to hear.

After each turn, a separate classifier (Gemini) reports scam behaviours such as gift-card payment, arrest threats, secrecy or requests for codes, each backed by the caller's exact words. Plain code, not either model, decides. A scam ends the call with a polite excuse, blocks the number and produces an evidence card with the transcript. Ordinary callers are put through. Bank and government callers are never put through automatically.

Phone numbers and SMS alerts are simulated in this demo.

---

## 02 Screenshots

Upload these five files from `docs/screenshots/` (address bars cropped out; they are also embedded in the PDF and the README):

1. `01-caller-room-setup.png`: **Caller view: creating a private room.** The caller creates a room and shares the code; the phone-style screen shows "Receiver offline" until the receiver joins.
2. `02-receiver-call-lists.png`: **Receiver's trust and block lists.** Only the receiver can edit them; changes apply to the next call and blocked numbers take priority. Shown: trusted contact Tom and a blocked number.
3. `03-unknown-caller-screened.png`: **Unlisted number is screened.** +1 (416) 999-6669 is on neither list (one digit off the blocked +1 (416) 999-6666), so Hang On screens the call ("Your call is being screened") instead of ringing the receiver.
4. `04-trusted-caller-rings-receiver.png`: **Trusted number rings the receiver.** Accept and Decline, no screening. The "Simulated numbers" badge is visible.
5. `05-call-ended.png`: **Call ended.** Caller's view after the trusted call.

All phone numbers are simulated and audio is browser audio only (the footers read "Browser audio, No carrier call").

---

## 03 Repository

**Repository URL:** https://github.com/AlexanderMenassa/BosonAI-Hackathon  (public, branch `main`)

**Commit SHA or tag to judge:** [[output of `git rev-parse HEAD` after your final push]]. Anything pushed after it will not be seen.

**What the judges find in the repo:** source in `hangon/`, setup and run instructions plus three real example inputs and outputs in `README.md`, scripted example calls in `scenarios/`, 200+ offline tests in `tests/`, `.env.example` listing every setting by name (no values).

### Before you push (run from the repo root, PowerShell or bash)

1. **Nothing secret or local is tracked.** This should print nothing (`.env.example` is fine):
   ```
   git ls-files | grep -E "(^|/)\.env$|\.db$|^reports/|__pycache__"
   ```
   PowerShell: `git ls-files | Select-String -Pattern '(^|/)\.env$|\.db$|^reports/|__pycache__'`
2. **Keys were never committed, even in old history.** Each should print nothing:
   ```
   git log --all --oneline -- .env
   git log --all --oneline -G"AIza[0-9A-Za-z_-]{20,}"
   git log --all --oneline -G"bai-[A-Za-z0-9_-]{12,}"
   ```
   If any prints a commit, rotate **both** the Boson and Gemini keys now, because a public repo exposes its whole history.
3. **Swap in the new docs:** `git mv README.md docs/ROADMAP.md`, then add the new `README.md` and `.env.example`. (The old README is a planning roadmap and says things like "browser audio is planned"; the links inside it that point to `README_CLAUDE.md` will need a `../`.)
4. **Fresh-clone test, as a judge would:** clone into an empty folder, create a venv, `pip install -r requirements.txt`, `python -m pytest -q`, then run the first sim command from the README. No `.env` should be needed.
5. **Pin it:** `git tag -a submission-v1 -m "Hackathon submission"` and `git push origin submission-v1`, then put the tag or SHA above.
6. **Tidy stale files:** `High Level Process Flow Chart.png` shows the original design (scammers stalled, local LLM, evidence tool calls) and no longer matches the build; delete or replace it (the README has an accurate diagram). `AGENTS.md` still says "Browser audio is planned, not implemented".

**Needed by the README as written:** the automatic voice registration (`hangon/voices.py` with `ensure_voice`, plus the matching `voice.py` and `config.py`) and `versatile-v05-s1.mp3` / `.txt` committed. Confirmed present in the repo.

---

## 04 How you built it (185 words)

**Higgs Realtime (`higgs-realtime`)** is Walter's live voice over a server-side WebSocket. We turn automatic replies off (`create_response: false`) so our code decides each turn before the model speaks, and send each reply's role as per-reply instructions. When the caller talks over Walter, we flush playback and truncate the stored reply (`conversation.item.truncate`) to what was actually heard.

**Higgs STT (`higgs-stt-3.1`)** transcribes each caller turn inside the realtime session and feeds the classifier.

**Boson Voices API** registers a reference clip from the hackathon library as a custom elderly voice, automatically at startup under each user's own key.

**Hard and novel:**
- *Separated authority:* Higgs speaks, Gemini assesses, code decides, so a caller cannot talk the agent into a transfer.
- *Grounded evidence:* every behaviour the classifier reports must quote the caller verbatim; unmatched quotes are dropped, so it cannot invent a scam.
- *No dead air:* classification runs in the background. A late verdict is applied after Walter's current line, and a stale "legit" result can never forward a caller whose newer turn sounded like a scam.
- A Gemini-driven red-team harness plays scammers from written briefs.

---

## 05 Team information

| Name | GitHub | Worked on |
| --- | --- | --- |
| Alexander Menassa | [@AlexanderMenassa](https://github.com/AlexanderMenassa) | Backend: all of the logic behind the call, including the decision engine, the judge and classifier, the persona, and the voices. |
| Baiwei Zhong | [@vito-zbw](https://github.com/vito-zbw) | Frontend and the virtual calling system, including the messages. |

**Known limitations and things judges should know:**

- Phone numbers, the telephone network and SMS / guardian alerts are **simulated**. Calls enter through the browser or terminal, not a real phone line, and caller ID can be spoofed, so a trusted number is not identity verification.
- Evaluated only on a small synthetic set of scripted and red-team calls. This is not evidence of real-world scam-detection accuracy; false positives and negatives are possible.
- With Gemini, call transcripts leave the machine; an Ollama option keeps them local.
- Background classification is wired into the terminal voice call and simulator. The browser demo still waits for the classifier before Walter replies.
- The browser demo was tested live on two devices, a phone and a laptop. Networks that block direct WebRTC connections may need a TURN relay, which is supported but must be configured (see `docs/PHASE-2B-SETUP.md`).
- English-first; other languages untested. An evidence-logging tool via Higgs function calling was planned but not built.

---

## Looking ahead (extra section; paste wherever the form has room for it)

Hang On proves the core loop: answer, listen, judge, decide. These are the next steps toward something a family could rely on.

- **Answer in the protected person's own voice.** Walter is a stand-in. With the protected person's consent, voice mimicry could copy their actual voice, so callers hear who they expect and a handoff to the real person sounds seamless. The pipeline for registering a custom voice from a short reference clip already exists (`hangon/voices.py`).
- **Know the person, catch scams faster.** Add personal details, such as their bank, pharmacy, doctor and family members' names, and check callers' claims against them. A "grandson" nobody has heard of, or a "bank" they do not use, can be flagged sooner. The details would stay private and never be revealed to a caller.
- **A fast model and a real phone number.** Run classification on a faster model to shorten the delay, and connect Hang On to a real phone number through a telephony provider. Proper testing needs real calls, with real latency, audio quality and accents, which a browser demo cannot reproduce.
- **Automatic trusted and blocked lists.** Import the phone's contacts as trusted numbers, with permission, and fill the blocklist from known scam numbers, instead of managing lists by hand. Caller ID can be spoofed, so a trusted number should still never be put through for high-stakes requests without checks.
- **Guardian check-ups.** The system already sends the same summaries to an optional guardian, so a family member can check on the vulnerable person. In this demo those messages are simulated, and we did not demo or test them rigorously. Next: real delivery and thorough testing.
- **Bug fixes and hardening.** Fix known issues, for example making the browser demo classify in the background like the voice call so Walter never pauses, and test more scam types, languages and live calls.
