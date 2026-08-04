# Sync Tool on macOS

## Setup — one command, once

Copy the `sync-tool` folder to your Mac. Open **Terminal**
(Spotlight → type "Terminal" → Return), then drag-and-drop:

1. Type `bash ` (with a space)
2. Drag `mac-setup.sh` from the folder into the Terminal window
3. Press Return

It looks like this:

```
bash /Users/you/sync-tool/mac-setup.sh
```

The script does everything: makes the app launchable, clears the macOS security
flag, installs Python's numpy into a private environment inside the folder, and
installs ffmpeg if Homebrew is present. It prints a tick for each step.

## Then: double-click `SyncTool.app`

That's it, from then on. No Terminal, no commands.

The app checks its own setup, starts the engine, opens your browser at
<http://localhost:8765>, and shows a small **Sync Tool is running** window.
Click **Quit** in that window when you're finished.

---

## Why one Terminal command is unavoidable

macOS won't let a file copied from Windows or unzipped from a download mark
itself as runnable — that's a security feature, not something the app can switch
off from the inside. `mac-setup.sh` is the one thing that flips that bit, and
`bash …` runs it even though it isn't executable yet. After that everything is
graphical.

(If you clone with `git` on a Mac instead of copying from Windows, the permission
usually survives and you can skip straight to double-clicking the app.)

---

## If something goes wrong

**"SyncTool.app cannot be opened because it is from an unidentified developer"**
Run `mac-setup.sh` — it clears the flag that causes this. If it still appears,
right-click the app → **Open** → **Open**. Once only.

**Nothing happens when you double-click the app**
The setup step was skipped. Run the `bash …` command above.

**"Sync Tool needs Python 3"**
Click **Install Python** in the dialog; macOS installs its developer tools
(a few minutes). Reopen the app afterwards.

**"Sync Tool needs ffmpeg"**
Click **Install ffmpeg** and it runs Homebrew for you. If you don't have
Homebrew, the app offers to open <https://brew.sh> — install that first (one
paste into Terminal), then reopen Sync Tool.

**Something else**
The app writes `.synctool-launch.log` inside the sync-tool folder. It's a plain
text file; open it with TextEdit and the error will be at the bottom.

---

## Exporting to Premiere / Final Cut / Resolve

Same as anywhere. The exported `synced_timeline.xml` uses proper Mac file paths,
so `/Users/you/Footage/…` resolves correctly:

- **Premiere Pro** — File › Import… → `synced_timeline.xml`
- **DaVinci Resolve** — File › Import › Timeline › Import AAF/EDL/XML…
- **Final Cut Pro 7** — File › Import › XML

---

## Honest status

The app bundle, the setup script and the whole sync pipeline were tested on
Linux, which shares macOS's file paths, shell and ffmpeg handling — the launcher
was run exactly as macOS runs it, with the Mac dialogs stubbed, and it started
the server, opened the browser and quit cleanly.

It has **not** been run on a real Mac yet. If something misbehaves, the launch
log named above is the place to look, and it's worth reporting.
