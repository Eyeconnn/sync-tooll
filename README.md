# Sync Tool

Waveform-syncs camera footage to separately-recorded audio when there is **no
timecode** — including the awkward case where the camera clocks are simply wrong.

Exports a timeline for **Premiere Pro**, **DaVinci Resolve**, Final Cut 7 or
Media Composer. Runs entirely on your own machine; nothing is uploaded anywhere.

> **Status: early prototype.** Validated end-to-end on one real shoot (see
> [Tested with](#tested-with)). Treat it as a promising starting point rather than
> a finished product, and check its work before trusting a delivery to it.

---

## Why this exists

Dual-system sound without timecode is common on small shoots: a camera rolling
independently, a wireless kit recording clean ISO tracks, and no shared clock
between them. Most sync tools assume either matching timecode or clocks that are
roughly right.

This one assumes neither. It works out each camera's clock error from the audio
itself, so a body whose clock is over an hour out still lands in the right place.

## What it does

1. Scans the folders you choose and reads each file's duration, frame rate,
   creation time and audio channel layout.
2. Works out **each camera's constant clock offset** by matching a handful of
   clips against your reference audio.
3. Matches every clip against the individual reference recordings to find its
   position and which mic it belongs to.
4. Refines confident matches to sample accuracy.
5. Lets you check, listen to and hand-correct anything doubtful.
6. Exports a placement CSV plus a DaVinci Resolve script that builds the timeline.

---

## Requirements

Runs on **Windows, macOS and Linux**.

- **Python 3.9+** with **numpy** → `pip install numpy`
- **ffmpeg 6.1 or newer** (both `ffmpeg` and `ffprobe`)

The ffmpeg version matters. Builds before 6.1 cannot decode Canon's `ipcm` audio
at all. There's a raw-PCM fallback for that case, but a current build avoids the
problem. If ffmpeg is missing, the app detects it, explains what's wrong, and can
install it for you through winget / chocolatey / scoop.

### Installing ffmpeg

The tool shells out to ffmpeg to read every file, so nothing works without it.

| Platform | Command |
|---|---|
| **macOS** | `brew install ffmpeg` ([Homebrew](https://brew.sh)) |
| **Windows** | `winget install Gyan.FFmpeg` |
| **Linux** | `sudo apt install ffmpeg` |

If it's missing the app says so, and its **Install it for me** button runs the
right package manager for your platform — Homebrew on a Mac, winget/Chocolatey/
Scoop on Windows, apt/dnf/snap on Linux. It also looks in the usual install
locations (`/opt/homebrew/bin`, `/usr/local/bin`, Homebrew's Cellar, MacPorts…),
so a fresh install is found even before your PATH catches up.

Already have ffmpeg elsewhere? Point at it:

```bash
export SYNCTOOL_FFMPEG=/path/to/ffmpeg
export SYNCTOOL_FFPROBE=/path/to/ffprobe
```

## Running it

- **macOS / Linux** — double-click **`start-sync-tool.command`** (first time:
  `chmod +x start-sync-tool.command`). It checks for Python and numpy, installing
  numpy if needed.
- **Windows** — double-click **`Start Sync Tool.bat`**.

Or from a terminal:

```
cd SyncTool
python3 server.py
```

Then use <http://localhost:8765>.

> On macOS the first double-click may be blocked by Gatekeeper. Right-click the
> file › Open, or run it from Terminal once.

> Don't open `ui.html` by double-clicking it — it has to be served by
> `server.py`, or the browser blocks it from reaching the engine.

---

## Workflow

### 1 · Add media

Add as many folders as you like; footage and audio can sit on different drives.
Pick a directory and the tool lists every sub-folder beneath it that contains
media — with file counts, type and channel count — so you can tick only the ones
you want. A folder you pick explicitly is used as-is and won't drag in its children.

### 2 · Set up tracks

Per folder: **role** (`reference` = your separate audio, the timing truth;
`camera` = footage synced to it), **sync by**, **track**, **track name**, and
**sync channels**.

#### Sync by: audio, or another camera's timecode

- **its own audio** (default) — the clip is placed by waveform-matching against
  your reference recordings.
- **timecode of…** — the clip inherits its position from a camera that *was*
  waveform-synced, using the timecode both cameras share.

The second option is for the common two-camera case: bodies jammed to the same
timecode, but only one carrying usable audio (say the A-cam fed from the wireless
receiver while the B-cam has only a scratch mic, or none). Sync the A-cam by
waveform, then set the B-cam to *timecode of → Camera A*.

It works by learning the relationship between timecode and timeline position from
the A-cam's confidently-placed clips, then applying that same offset to every
B-cam clip via its own embedded timecode. No audio is needed from the B-cam at all.

**It also tells you whether the jam was real.** The review step reports how closely
those clips agree. Agreement within a frame or two means the cameras were genuinely
jammed. A large spread means they weren't — free-running timecode that happens to
exist but means nothing — and you get a warning rather than a silently wrong
timeline. (Both test cameras here were free-run: the tool measured ±176 s of
disagreement and flagged it.)

Clips placed this way show as **from timecode** in the review, and any clip missing
timecode falls back to its creation time and is flagged for checking.

Click **Channels** on a camera to see what its audio actually holds — level in dB,
a waveform sparkline, and automatic *very quiet* / *duplicate of ch N* flags. Click
any waveform to hear that moment.

This is the setting that most affects quality. On the test shoot the R5 II carried
the wireless feed on channels 1–2 and a scratch mic ~10 dB down on 3–4 (ch4 an
exact duplicate of ch3). Choosing the wrong pair collapses the match rate.

You can also attach Resolve metadata per folder — Scene, Shot, Angle, Reel,
Keywords, Comments and a clip colour.

### 3 · Check the sync

Every clip is colour-coded: **locked** (sample-accurate), **close** (±0.5 s,
placed from the clock model), **needs a look** (no confident match), **adjusted**.

For real verification press **Open sync editor** — a full-screen workspace with:

- Large stacked waveforms, clip above reference, shared time axis, zoom 30 s → 1 s
- Drag to slide the clip, or nudge with ← → (one frame) and Shift+← → (0.1 s)
- **Play both together** — in sync you hear one sound, out of sync an echo.
  Sample-accurate playback that follows your adjustment live
- A filtered queue (*Needs attention*, *No match only*…) to work through

### 4 · Export

Set a sequence name and timeline frame rate, then export. You get three files:

| File | What it's for |
|---|---|
| `synced_timeline.xml` | **FCP7 XML (xmeml v4)** — the timeline itself. Imports into Premiere Pro, DaVinci Resolve, Final Cut 7 and Media Composer. |
| `sync_placement.csv` | Every clip with its position, status, match strength and metadata — for reference or your own tooling. |
| `build_timeline_in_resolve.py` | Optional Resolve route; also applies clip metadata and colours. |

**Premiere Pro** — File › Import… → pick `synced_timeline.xml`. Premiere builds a
sequence with your tracks, names and clip positions intact.

**DaVinci Resolve** — File › Import › Timeline › Import AAF/EDL/XML… → the same
file. (Or run the Python script in *Workspace › Console › Py3*, which additionally
writes Scene/Shot/Angle/Reel metadata and clip colours onto the media pool items.)

All of it is non-destructive: the XML references your media where it already sits,
and no media file is ever modified.

---

## How the sync works

1. **Envelopes** — audio is decoded at a low rate into a high-passed log-RMS
   envelope (~100 Hz), cached so re-runs are quick.
2. **Device clock offset** — a few clips are searched *globally* against a summed
   reference bed. A clock error is a **constant**, so the median of those hits
   gives the camera's offset. This is what catches an error of hours.
3. **Per-clip match** — each clip is then correlated against each **individual**
   reference in a narrow window around its corrected time. Matching per-reference
   rather than against the summed bed matters — one strong mic gets diluted in a sum.
4. **Fine refine** — sample-level correlation at 8 kHz for frame accuracy.
5. **Fallback** — clips that won't lock still get `creation_time + device offset`,
   usually within half a second.

---

## Tested with

One full shoot: **131 camera clips, 41 mic ISO recordings, ~152 GB**, no timecode
anywhere.

| Kit | Notes |
|---|---|
| **Canon EOS R5 Mark II** | 4 audio channels — 1–2 fed from the wireless receiver, 3–4 on-board scratch. Audio codec `ipcm`, which needs ffmpeg 6.1+ (or the built-in raw-PCM fallback). |
| **Canon EOS R6 Mark II** | Single audio channel, codec `twos` (big-endian PCM). Mixed 25p / 50p clips. |
| **DJI Mic 3 kit** | Four transmitters recording ISO WAVs. BWF origination timestamps are accurate, but `TimeReference` is 0 — i.e. no usable timecode. |

Results from a cold start, with no hints given:

- Rediscovered the R6 II's clock error to within **0.04 s** and the R5 II's to
  within **0.32 s** of values derived by hand.
- Both errors were substantial — one body was **over an hour** out, the other
  nearly three minutes.
- **62 of 131** clips locked to sample accuracy; the rest landed within ~0.5 s via
  the clock model.
- Locked clips matched the correct individual transmitter, with the clip and
  reference waveforms correlating at **0.92**.

---

## Known limits

- **One kit, one shoot.** Audio codec handling in particular varies by
  manufacturer; `ipcm` was a surprise from the R5 II and others will differ.
- **Reference-to-reference alignment** is only as good as the recorder's own
  timestamps. The DJI Mic 3 writes whole-second BWF origination times, so separate
  ISO files sit within ~0.5 s of each other in absolute terms.
- **Clips that won't lock** (music, silence, wild track) fall back to the clock
  model. They're flagged red — check them in the editor.
- **Outlier dates** stretch the overview timeline. Stray recordings from other days
  will squash the real shoot into a sliver; exclude those folders for now.
- **XML import is verified structurally, not in every NLE.** The exported xmeml is
  well-formed and correct on inspection, and has been checked against a real 172-clip
  timeline, but it has not yet been opened in every host. Premiere and Resolve are
  the intended targets; report anything that imports oddly.
- **Resolve can't script multicam clips.** Blackmagic doesn't expose it, so the
  script builds the equivalent synced timeline instead.
- Runs from source; not packaged as an app.
- **macOS is supported but only lightly exercised.** The code is POSIX-clean and the
  whole pipeline was tested on Linux (same path handling, same launcher, same
  ffmpeg discovery), but it has not been run on a Mac end to end. The likely
  rough edges are Gatekeeper on the `.command` launcher and tkinter missing from
  some Python builds — the in-app folder browser covers the latter.

## Files

| File | Purpose |
|---|---|
| `syncengine.py` | Scanning, audio extraction, correlation, clock-offset detection |
| `server.py` | Local web server and JSON API |
| `ui.html` | The interface |
| `exporters.py` | FCP7 XML (xmeml) timeline export for Premiere / Resolve / FCP7 |
| `resolve_template.py` | Template for the exported Resolve script |
| `Start Sync Tool.bat` | Windows launcher |
| `start-sync-tool.command` | macOS / Linux launcher |

## Licence

MIT — see [LICENSE](LICENSE).
