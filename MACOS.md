# Sync Tool on macOS

## Getting it running

Copy the `sync-tool` folder to your Mac, then:

1. **Delete the `SyncTool.app` that came in the folder** (it lost its permissions
   crossing over from Windows — that's what causes *"the application can't be
   opened"*).
2. **Double-click `SyncTool.app.zip`.** macOS unzips it and produces a working
   `SyncTool.app` in the same folder.
3. **Double-click that `SyncTool.app`.**

That's it. The app sets itself up on first run — Python, numpy in a private
environment inside the folder, and ffmpeg — asking permission with ordinary Mac
dialogs. It then starts the engine, opens your browser at
<http://localhost:8765>, and shows a small **Sync Tool is running** window.
Click **Quit** there when you're finished.

> **Why the zip?** A zip file can carry Unix permissions; a Windows folder copy
> cannot. The `.app` inside the archive already has its "runnable" flag set, so
> unzipping on the Mac produces a working app with nothing to fix.

### If you'd rather not use the zip

`mac-setup.sh` repairs the plain `SyncTool.app` instead. In **Terminal**, type
`bash ` (with a space), drag `mac-setup.sh` in from Finder, and press Return:

```
bash /Users/you/sync-tool/mac-setup.sh
```

It restores the permission, clears the macOS quarantine flag, sets up numpy and
installs ffmpeg if Homebrew is present, printing a tick per step. Afterwards the
app double-clicks normally.

---

## If something goes wrong

**"The application SyncTool.app can't be opened"** (or *"...cannot be opened
because it is damaged"*)
The app's runnable permission was stripped — almost always because the folder was
copied from Windows. Delete that `SyncTool.app`, double-click
`SyncTool.app.zip`, and use the app it produces. Or run `bash mac-setup.sh`.

**"SyncTool.app cannot be opened because it is from an unidentified developer"**
This is Gatekeeper, and it's different from the above. Right-click the app →
**Open** → **Open**. Once only. (`mac-setup.sh` also clears the flag that
triggers it.)

**Nothing happens when you double-click the app**
Use the zip, or run the `bash mac-setup.sh` command above.

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
