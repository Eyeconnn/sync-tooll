"""
server.py - local web app for dual-system audio sync.

Run:  python server.py            (then open http://localhost:8765)
Deps: numpy, and ffmpeg/ffprobe on PATH.

Nothing is uploaded anywhere - this is a localhost server reading your own disk.
"""

import json, os, subprocess, sys, threading, traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import syncengine as se
import exporters
import numpy as np

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))

STATE = {
    "roots": [],       # one or more project folders
    "groups": [],      # discovered folders + user config
    "results": None,   # sync output
    "job": {"running": False, "done": 0, "total": 0, "label": "", "error": None},
    "install": {"running": False, "log": "", "ok": None},
}


def run_install_job(method):
    inst = STATE["install"]
    inst.update(running=True, log="", ok=None)

    def log(s):
        inst["log"] += s

    try:
        ok, _out = se.install_ffmpeg(method, log=log)
        inst["ok"] = ok
    except Exception as e:
        inst["log"] += f"\nERROR: {e}\n"
        inst["ok"] = False
    finally:
        inst["running"] = False


# ------------------------------------------------------------------ helpers --
def _media_in(dirpath):
    try:
        return [f for f in os.listdir(dirpath)
                if os.path.splitext(f)[1].lower() in (se.VIDEO_EXT | se.AUDIO_EXT)
                and os.path.isfile(os.path.join(dirpath, f))]
    except OSError:
        return []


def subfolders(root):
    """Every folder at or under `root` that actually holds media, so the user
    can tick which ones to scan instead of taking the whole tree."""
    out = []
    for dirpath, _d, _f in os.walk(root):
        if se.CACHE_DIR in dirpath:
            continue
        media = _media_in(dirpath)
        if not media:
            continue
        probe = None
        for cand in sorted(media)[:3]:
            probe = se.media_info(os.path.join(dirpath, cand))
            if probe:
                break
        rel = os.path.relpath(dirpath, root)
        out.append({
            "path": os.path.abspath(dirpath),
            "rel": "." if rel == "." else rel,
            "name": os.path.basename(dirpath) or dirpath,
            "count": len(media),
            "kind": probe["kind"] if probe else "unknown",
            "channels": probe["n_audio"] if probe else 0,
            "depth": 0 if rel == "." else rel.count(os.sep) + 1,
        })
    out.sort(key=lambda x: x["rel"].lower())
    return out


def discover(roots):
    """Group media by containing folder.

    A root that directly contains media is taken as-is (the user picked that
    exact folder); otherwise we walk it. This stops an explicitly chosen folder
    from also dragging in its own sub-folders."""
    groups = {}
    for root in roots:
        dirs = [root] if _media_in(root) else [d for d, _s, _f in os.walk(root)]
        for dirpath in dirs:
            if se.CACHE_DIR in dirpath:
                continue
            media = _media_in(dirpath)
            if not media:
                continue
            # probe several files - one unreadable file must not mislabel a folder
            probe = None
            for cand in sorted(media)[:5]:
                probe = se.media_info(os.path.join(dirpath, cand))
                if probe:
                    break
            rel = os.path.relpath(dirpath, root)
            if rel == ".":
                # the user picked this exact folder - show the last two parts so
                # four different ".../edited" folders don't all read as "edited"
                parts = [p for p in os.path.abspath(dirpath).replace("\\", "/").split("/") if p]
                label = "/".join(parts[-2:]) if len(parts) > 1 else dirpath
            else:
                label = os.path.basename(root) + os.sep + rel
            groups[os.path.abspath(dirpath)] = {
                "path": os.path.abspath(dirpath),
                "folder": label,
                "root": root,
                "count": len(media),
                "kind": probe["kind"] if probe else "unknown",
                "channels": probe["n_audio"] if probe else 0,
                "fps": probe["fps"] if probe else None,
                "codecs": probe["audio_codecs"] if probe else [],
                "sample": os.path.join(dirpath, sorted(media)[0]),
                # sensible defaults the user can override
                "role": "reference" if (probe and probe["kind"] == "audio") else "camera",
                "track": 1,
                "sync_channels": [0, 1] if (probe and probe["n_audio"] >= 2) else [0],
                "sync_mode": "waveform",
                "tc_source": "",
                "has_timecode": bool(probe and probe.get("timecode")),
                "timecode": (probe or {}).get("timecode"),
                "enabled": True,
            }
    out = [groups[k] for k in sorted(groups)]

    # Default track names must be unique and meaningful. "edited" x4 is useless,
    # so fall back to parent/child when plain basenames collide.
    from collections import Counter
    base = {g["path"]: os.path.basename(g["path"]) or g["path"] for g in out}
    dupes = {n for n, c in Counter(base.values()).items() if c > 1}
    for g in out:
        b = base[g["path"]]
        if b in dupes:
            parent = os.path.basename(os.path.dirname(g["path"]))
            g["name"] = f"{parent} {b}".strip() if parent else b
        else:
            g["name"] = b

    # Give each folder its own track by default - otherwise every mic stacks
    # onto A1 and the timeline is unreadable.
    nv = na = 0
    for g in out:
        if g["kind"] == "video":
            nv += 1
            g["track"] = nv
        else:
            na += 1
            g["track"] = na
    return out


def _shortcuts():
    """Quick jumps for the folder browser - crucially including mounted drives,
    which is where footage usually lives."""
    out, home = [], os.path.expanduser("~")
    if os.name == "nt":
        import string
        for d in string.ascii_uppercase:
            root = f"{d}:\\"
            if os.path.exists(root):
                out.append({"label": f"{d}: drive", "path": root, "kind": "drive"})
    else:
        # external / removable drives: /Volumes on macOS, /media & /mnt on Linux
        for base in ("/Volumes", "/media", "/mnt", f"/media/{os.path.basename(home)}",
                     "/run/media"):
            if not os.path.isdir(base):
                continue
            try:
                for name in sorted(os.listdir(base)):
                    p = os.path.join(base, name)
                    if not os.path.isdir(p) or name.startswith("."):
                        continue
                    # skip the boot volume symlink macOS puts in /Volumes
                    if os.path.realpath(p) == "/":
                        continue
                    out.append({"label": name, "path": p, "kind": "drive"})
            except OSError:
                pass
    for label, sub in (("Home", ""), ("Desktop", "Desktop"),
                       ("Documents", "Documents"), ("Downloads", "Downloads"),
                       ("Movies", "Movies")):
        p = os.path.join(home, sub) if sub else home
        if os.path.isdir(p):
            out.append({"label": label, "path": p, "kind": "home"})
    if os.name != "nt":
        out.append({"label": "Top level (/)", "path": "/", "kind": "root"})
    return out


TCC_HELP = ("macOS is withholding access to this drive.\n\n"
            "Open System Settings › Privacy & Security › Full Disk Access, "
            "switch on the app that launched Sync Tool (Terminal, or SyncTool.app), "
            "then quit and reopen Sync Tool.")


def _is_mac_volume(path):
    """True for /Volumes/<drive> and anything inside it - the only place where an
    empty listing is meaningful evidence of macOS withholding permission."""
    return sys.platform == "darwin" and os.path.abspath(path).startswith("/Volumes/")


def access_report(path):
    """Can we really read this folder? macOS returns an EMPTY listing for a
    TCC-protected volume instead of raising, so 'no error' is not proof.

    An empty listing only implies blocking on a macOS external volume - an empty
    folder anywhere else is just an empty folder.
    """
    info = {"path": path, "exists": os.path.isdir(path), "entries": 0,
            "readable": False, "blocked": False, "detail": ""}
    if not info["exists"]:
        info["detail"] = "not mounted"
        return info
    try:
        names = os.listdir(path)
        info["entries"] = len(names)
        info["readable"] = True
        if len(names) == 0 and _is_mac_volume(path):
            info["blocked"] = True
            info["detail"] = "empty listing - permission is being withheld"
        else:
            info["detail"] = f"{len(names)} entries"
    except PermissionError:
        info["blocked"] = True
        info["detail"] = "permission denied"
    except OSError as e:
        info["detail"] = str(e)
    return info


def list_dirs(path):
    """Server-side folder browser (also the fallback when no native dialog exists)."""
    err = None
    blocked = False
    if not path:
        path = os.path.expanduser("~")
    path = os.path.abspath(path)
    dirs = []
    n_entries = 0
    try:
        names = sorted(os.listdir(path))
        n_entries = len(names)
        for d in names:
            if d.startswith("."):
                continue
            full = os.path.join(path, d)
            try:
                if os.path.isdir(full):
                    dirs.append(full)
            except OSError:
                continue
    except PermissionError:
        err, blocked = TCC_HELP, True
    except FileNotFoundError:
        err = "That folder no longer exists - was the drive unplugged?"
    except OSError as e:
        err = str(e)

    # Silent denial: the folder opened fine but came back completely empty.
    if err is None and n_entries == 0 and _is_mac_volume(path):
        err, blocked = TCC_HELP, True

    parent = os.path.dirname(path.rstrip(os.sep)) or ("/" if os.name != "nt" else "")
    return {"path": path, "parent": (parent if parent != path else None),
            "dirs": dirs, "shortcuts": _shortcuts(), "error": err,
            "blocked": blocked, "entries": n_entries}


def native_pick_folder():
    """Open the OS folder picker.

    On macOS this uses the system panel via AppleScript. That matters for more
    than looks: picking a folder in Apple's own panel is treated as user intent,
    so the app is granted access to that folder even when a blanket permission
    hasn't been given. It is the supported way past a blocked external drive -
    no security settings need changing. It also avoids depending on tkinter,
    which many Python builds ship without.
    """
    if sys.platform == "darwin":
        script = ('set f to choose folder with prompt "Choose a media folder"\n'
                  'return POSIX path of f')
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True)
        p = (r.stdout or "").strip()
        if p:
            return p.rstrip("/") or "/"
        if "User canceled" in (r.stderr or ""):
            return None
        # fall through to tkinter if osascript was unavailable

    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        p = filedialog.askdirectory(title="Choose a media folder")
    finally:
        root.destroy()
    return p or None


def run_sync_job(cfg):
    """Background worker: scan configured folders, then sync each camera group."""
    job = STATE["job"]
    try:
        job.update(running=True, done=0, total=0, label="scanning", error=None)
        refs, cams = [], []
        for g in cfg["groups"]:
            if not g.get("enabled", True):
                continue
            items = [i for i in se.scan(g["path"], recursive=False)]
            if g["role"] == "reference":
                refs += [(g, i) for i in items]
            else:
                cams.append((g, items))

        ref_objs = [se.Reference(i) for _g, i in refs]
        if not ref_objs:
            raise RuntimeError("No reference audio selected. Mark at least one folder as 'reference'.")

        job["total"] = sum(len(i) for _g, i in cams)
        by_name = {i["name"]: g for g, i in refs}

        def gmeta(g):
            return {"track_name": g.get("track_name") or os.path.basename(g["folder"]),
                    "meta": g.get("meta") or {}}

        out = {"references": [dict({"name": r.name, "start_s": r.start, "dur": r.dur,
                                    "track": by_name[r.name]["track"],
                                    "group": by_name[r.name]["folder"],
                                    "path": r.info["path"]},
                                   **gmeta(by_name[r.name])) for r in ref_objs],
               "groups": []}

        # Phase 1 - cameras synced by waveform against the reference audio.
        # Phase 2 - cameras that inherit their position from a jammed camera's
        # shared timecode. Their clips carry no usable audio of their own, so
        # they are placed from the timecode->timeline relationship learned in
        # phase 1 rather than by correlation.
        wave_cams = [(g, i) for g, i in cams if (g.get("sync_mode") or "waveform") != "timecode"]
        tc_cams = [(g, i) for g, i in cams if (g.get("sync_mode") or "waveform") == "timecode"]

        for g, items in wave_cams:
            job["label"] = g["folder"]
            def prog(i, n, name, _g=g):
                job["done"] += 1
                job["label"] = f"{_g['folder']}: {name}"
            res = se.sync(items, ref_objs, streams=g["sync_channels"],
                          window=240, progress=prog)
            # keep each clip's embedded timecode alongside its placement
            tc_by_name = {i["name"]: i.get("tc_s") for i in items}
            for c in res["clips"]:
                c["tc_s"] = tc_by_name.get(c["name"])
            res["group"] = g["folder"]
            res["role"] = g["role"]
            res["track"] = g["track"]
            res["sync_mode"] = "waveform"
            res.update(gmeta(g))
            out["groups"].append(res)

        for g, items in tc_cams:
            job["label"] = f"{g['folder']}: linking by timecode"
            src_key = g.get("tc_source")
            src = next((x for x in out["groups"]
                        if x["group"] == src_key or x.get("track_name") == src_key), None)
            if src is None:
                src = out["groups"][0] if out["groups"] else None
            if src is None:
                raise RuntimeError(
                    f"{g['folder']} is set to sync by timecode, but no waveform-synced "
                    "camera was available to learn the timecode offset from.")
            delta, spread, used = se.tc_mapping(src["clips"])
            job["done"] += len(items)
            if delta is None:
                raise RuntimeError(
                    f"Could not read timecode from {src.get('track_name') or src['group']}, "
                    "so there is nothing to link to.")
            clips = se.place_by_timecode(items, delta, spread)
            for c, i in zip(clips, items):
                c["tc_s"] = i.get("tc_s")
            out["groups"].append(dict(
                group=g["folder"], role=g["role"], track=g["track"],
                sync_mode="timecode", tc_source=src.get("track_name") or src["group"],
                tc_delta_s=round(delta, 3), tc_spread_s=round(spread or 0, 3),
                tc_clips_used=used, device_offset_s=round(delta, 2),
                clips=clips, **gmeta(g)))

        STATE["results"] = out
        job.update(running=False, label="done")
    except Exception as e:
        traceback.print_exc()
        job.update(running=False, error=str(e))


def inspect_pair(clip_path, stream, ref_path, position_s, ref_start_s, span=6.0):
    """Return aligned envelope snippets so the UI can draw camera vs reference."""
    cam = se.envelope(clip_path, stream)
    ref = se.envelope(ref_path, 0)
    lag = position_s - ref_start_s                 # where the clip sits in the ref
    n = int(span * se.FR)
    c = cam[:n]
    i = int(lag * se.FR)
    r = ref[max(0, i): max(0, i) + n]
    def norm(a):
        a = np.asarray(a, dtype=float)
        if not len(a):
            return []
        a = a - a.mean()
        m = np.max(np.abs(a)) or 1.0
        return [round(float(v), 3) for v in (a / m)[:n]]
    return {"cam": norm(c), "ref": norm(r), "fr": se.FR}


# ------------------------------------------------------------------- export --
def export_files(outdir):
    """Write the placement CSV + a DaVinci Resolve build script."""
    res = STATE["results"]
    if not res:
        raise RuntimeError("Nothing to export - run a sync first.")
    os.makedirs(outdir, exist_ok=True)
    META_KEYS = ["scene", "shot", "angle", "reel", "comments", "keywords", "color"]

    def meta_cols(src):
        m = src.get("meta") or {}
        return {k: m.get(k, "") for k in META_KEYS}

    rows = []
    for g in res["groups"]:
        for c in g["clips"]:
            rows.append(dict(kind="video", track=g["track"], group=g["group"],
                             track_name=g.get("track_name") or g["group"],
                             file=os.path.basename(c["path"]), path=c["path"],
                             position_s=c["position_s"], dur_s=round(c["dur"], 3),
                             fps=c["fps"] or "", status=c["status"],
                             confidence=c["confidence"], matched_ref=c["matched_ref"] or "",
                             **meta_cols(g)))
    for r in res["references"]:
        rows.append(dict(kind="audio", track=r["track"], group=r["group"],
                         track_name=r.get("track_name") or r["group"],
                         file=os.path.basename(r["path"]), path=r["path"],
                         position_s=r["start_s"], dur_s=round(r["dur"], 3), fps="",
                         status="reference", confidence="", matched_ref="",
                         **meta_cols(r)))
    import csv
    csv_path = os.path.join(outdir, "sync_placement.csv")
    cols = ["kind", "track", "group", "track_name", "file", "path", "position_s", "dur_s",
            "fps", "status", "confidence", "matched_ref"] + META_KEYS
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        [w.writerow(r) for r in rows]

    out = {"csv": csv_path, "rows": len(rows), "written": [csv_path]}

    # --- FCP7 XML: imports into Premiere Pro, Resolve, FCP7, Media Composer ---
    seq = STATE.get("sequence_name") or "Synced Timeline"
    fps = float(STATE.get("timeline_fps") or 25)
    xml_path = os.path.join(outdir, "synced_timeline.xml")
    try:
        exporters.write_fcp7_xml(rows, xml_path, sequence_name=seq, fps=fps)
        out["xml"] = xml_path
        out["written"].append(xml_path)
    except Exception as e:
        out["xml_error"] = str(e)

    # --- Resolve script (kept: it can also apply metadata + clip colours) ---
    tpl = os.path.join(HERE, "resolve_template.py")
    if os.path.isfile(tpl):
        script_path = os.path.join(outdir, "build_timeline_in_resolve.py")
        with open(tpl, encoding="utf-8") as f:
            body = f.read()
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(body.replace("__CSV_PATH__", csv_path.replace("\\", "\\\\")))
        out["script"] = script_path
        out["written"].append(script_path)
    return out


# ------------------------------------------------------------------ server --
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "ui.html"), "rb") as f:
                return self._send(f.read(), ctype="text/html; charset=utf-8")
        if u.path == "/api/health":
            return self._send(se.tools_status())
        if u.path == "/api/diagnose":
            return self._send(se.diagnose())
        if u.path == "/api/install_progress":
            return self._send(dict(STATE["install"], status=se.tools_status()))
        if u.path == "/api/progress":
            return self._send(STATE["job"])
        if u.path == "/api/results":
            return self._send(STATE["results"] or {})
        if u.path == "/api/inspect":
            try:
                return self._send(inspect_pair(q["clip"][0], int(q.get("stream", ["0"])[0]),
                                               q["ref"][0], float(q["pos"][0]),
                                               float(q["refstart"][0])))
            except Exception as e:
                return self._send({"error": str(e)}, 400)
        if u.path == "/api/preview":
            try:
                data = se.preview_wav(q["path"][0], int(q.get("stream", ["0"])[0]),
                                      float(q.get("start", ["0"])[0]),
                                      float(q.get("dur", ["12"])[0]))
                return self._send(data, ctype="audio/wav")
            except Exception as e:
                return self._send({"error": str(e)}, 400)
        if u.path == "/api/browse":
            try:
                return self._send(list_dirs(q.get("path", [""])[0]))
            except Exception as e:
                return self._send({"error": str(e)}, 400)
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        try:
            if u.path == "/api/openprivacy":
                if sys.platform == "darwin":
                    os.system("open 'x-apple.systempreferences:com.apple.preference."
                              "security?Privacy_AllFiles' >/dev/null 2>&1 &")
                return self._send({"opened": sys.platform == "darwin"})
            if u.path == "/api/rescan":
                return self._send(se.rescan_tools())
            if u.path == "/api/install_ffmpeg":
                if STATE["install"]["running"]:
                    return self._send({"error": "install already running"}, 400)
                method = data.get("method") or (se.diagnose().get("fix") or "winget")
                if method in ("rescan",):
                    return self._send({"rescanned": True, **se.rescan_tools()})
                if method == "manual":
                    return self._send({"error": "No package manager found - "
                                                "install ffmpeg manually (see README)."}, 400)
                threading.Thread(target=run_install_job, args=(method,), daemon=True).start()
                return self._send({"started": True, "method": method})
            if u.path == "/api/pickfolder":
                try:
                    p = native_pick_folder()
                    return self._send({"path": p})
                except Exception as e:
                    return self._send({"error": f"No native dialog available ({e}). "
                                                "Use Browse instead."}, 400)
            if u.path == "/api/channels":
                path = data.get("path")
                if not path or not os.path.isfile(path):
                    return self._send({"error": "file not found"}, 400)
                return self._send(se.channel_preview(path, seconds=int(data.get("seconds", 45))))
            if u.path == "/api/subfolders":
                p = data.get("path")
                if not p or not os.path.isdir(p):
                    return self._send({"error": f"Folder not found: {p}"}, 400)
                folders = subfolders(p)
                if not folders:
                    rep = access_report(p)
                    if rep["blocked"]:
                        return self._send({"error": TCC_HELP, "blocked": True}, 400)
                    return self._send({"error":
                        "No video or audio files were found in that folder "
                        "(or any folder inside it).", "folders": []}, 400)
                return self._send({"root": os.path.abspath(p), "folders": folders})
            if u.path == "/api/checkaccess":
                vols = []
                base = "/Volumes"
                if os.path.isdir(base):
                    try:
                        for n in sorted(os.listdir(base)):
                            fp = os.path.join(base, n)
                            if os.path.isdir(fp) and os.path.realpath(fp) != "/":
                                vols.append(access_report(fp))
                    except OSError as e:
                        vols.append({"path": base, "detail": str(e), "blocked": True})
                return self._send({"volumes": vols, "help": TCC_HELP})
            if u.path == "/api/discover":
                roots = data.get("roots") or ([data["root"]] if data.get("root") else [])
                roots = [r for r in roots if r]
                bad = [r for r in roots if not os.path.isdir(r)]
                if bad:
                    return self._send({"error": "Folder not found: " + ", ".join(bad)}, 400)
                if not roots:
                    return self._send({"error": "Add at least one folder."}, 400)
                STATE["roots"] = roots
                STATE["groups"] = discover(roots)
                return self._send({"roots": roots, "groups": STATE["groups"]})
            if u.path == "/api/sync":
                if STATE["job"]["running"]:
                    return self._send({"error": "already running"}, 400)
                threading.Thread(target=run_sync_job, args=(data,), daemon=True).start()
                return self._send({"started": True})
            if u.path == "/api/nudge":
                for g in STATE["results"]["groups"]:
                    for c in g["clips"]:
                        if c["path"] == data["path"]:
                            c["position_s"] = round(float(data["position_s"]), 3)
                            c["status"] = "manual"
                            return self._send({"ok": True, "clip": c})
                return self._send({"error": "clip not found"}, 404)
            if u.path == "/api/export":
                default = STATE["roots"][0] if STATE["roots"] else os.getcwd()
                if data.get("sequence_name"):
                    STATE["sequence_name"] = data["sequence_name"]
                if data.get("fps"):
                    STATE["timeline_fps"] = float(data["fps"])
                return self._send(export_files(data.get("outdir") or default))
        except Exception as e:
            traceback.print_exc()
            return self._send({"error": str(e)}, 500)
        return self._send({"error": "not found"}, 404)


if __name__ == "__main__":
    st = se.tools_status()
    if st["ok"]:
        print("ffmpeg:", st["version"] or st["ffmpeg"])
    else:
        print("=" * 66)
        print(" ffmpeg / ffprobe NOT FOUND - the tool cannot read any media.")
        print("")
        print(" Install, then restart:")
        if se.IS_WIN:
            print("   winget install Gyan.FFmpeg        (or: choco install ffmpeg)")
            print("   or unzip a build from https://www.gyan.dev/ffmpeg/builds/")
            print("     to C:\\ffmpeg  (so C:\\ffmpeg\\bin\\ffmpeg.exe exists)")
            print("")
            print(" Already have it elsewhere? Point at it directly:")
            print("   set SYNCTOOL_FFMPEG=C:\\path\\to\\ffmpeg.exe")
            print("   set SYNCTOOL_FFPROBE=C:\\path\\to\\ffprobe.exe")
        elif se.IS_MAC:
            print("   brew install ffmpeg               (Homebrew: https://brew.sh)")
            print("   or download a static build from https://evermeet.cx/ffmpeg/")
            print("     and put ffmpeg + ffprobe in /usr/local/bin")
            print("")
            print(" Already have it elsewhere? Point at it directly:")
            print("   export SYNCTOOL_FFMPEG=/path/to/ffmpeg")
            print("   export SYNCTOOL_FFPROBE=/path/to/ffprobe")
        else:
            print("   sudo apt install ffmpeg           (or dnf / snap)")
            print("")
            print("   export SYNCTOOL_FFMPEG=/path/to/ffmpeg")
            print("   export SYNCTOOL_FFPROBE=/path/to/ffprobe")
        print(" Or press 'Install it for me' in the browser window.")
        print("=" * 66)
    url = f"http://localhost:{PORT}"
    print(f"Sync Tool running at {url}   (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
