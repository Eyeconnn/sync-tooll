"""
syncengine.py - dual-system audio sync engine.

Syncs camera clips to separate audio recordings by waveform, even when the
camera clocks are wrong or there is no timecode at all.

Pipeline
  1. scan()          ffprobe every file: duration, fps, creation time, channels
  2. envelopes       decode audio at low rate, cached; raw-PCM fallback for
                     codecs ffmpeg can demux but not decode (e.g. Canon 'ipcm')
  3. device offset   wide search of sample clips against a summed reference bed
                     -> constant per-device clock error (handles hours of error)
  4. per-clip match  windowed correlation against each INDIVIDUAL reference
                     -> best matching source + precise lag + confidence
  5. fine refine     sample-level correlation for frame accuracy
  6. fallback        clips that will not lock get created_time + device offset

Requires: ffmpeg/ffprobe on PATH (6.1+ recommended), numpy.
"""

import json, os, re, subprocess, hashlib, datetime, glob, platform, sys
from shutil import which
import numpy as np

FR        = 100      # envelope frames per second
FINE_FS   = 8000     # sample rate for fine refinement
CACHE_DIR = ".synccache"

VIDEO_EXT = {".mp4", ".mov", ".mxf", ".braw", ".avi", ".mkv", ".m4v"}
AUDIO_EXT = {".wav", ".bwf", ".aif", ".aiff", ".flac"}


# ------------------------------------------------------------ ffmpeg setup --
# ffmpeg/ffprobe are required. Look on PATH, then in an env var override, then
# in the usual Windows install locations, so users who unzipped a build without
# editing PATH still work.
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
EXE = ".exe" if IS_WIN else ""

_WIN_GUESSES = [
    r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin",
    r"C:\Program Files (x86)\ffmpeg\bin", r"C:\ProgramData\chocolatey\bin",
    os.path.expanduser(r"~\scoop\shims"),
]
_MAC_GUESSES = [
    "/opt/homebrew/bin",            # Homebrew on Apple silicon
    "/usr/local/bin",               # Homebrew on Intel
    "/opt/local/bin",               # MacPorts
    "/usr/bin",
    os.path.expanduser("~/bin"),
    "/Applications/ffmpeg",
]
_NIX_GUESSES = ["/usr/bin", "/usr/local/bin", "/snap/bin", os.path.expanduser("~/bin")]

_GUESSES = ([os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg")]
            + (_WIN_GUESSES if IS_WIN else _MAC_GUESSES if IS_MAC else _NIX_GUESSES))


def _package_manager_dirs(name):
    """Package managers install into versioned folders - glob for them. Needed
    because a freshly installed ffmpeg is not on the PATH that this already-running
    process inherited."""
    hits = []
    if IS_WIN:
        pats = (os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*"),
                os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*ffmpeg*"),
                r"C:\ProgramData\chocolatey\lib\ffmpeg*",
                r"C:\ffmpeg*")
    elif IS_MAC:
        pats = ("/opt/homebrew/Cellar/ffmpeg*", "/usr/local/Cellar/ffmpeg*",
                "/opt/local/var/macports/software/ffmpeg*")
    else:
        pats = ("/usr/lib/ffmpeg*", "/snap/ffmpeg*")
    for pat in pats:
        for root in glob.glob(pat):
            hits += glob.glob(os.path.join(root, "**", name + EXE), recursive=True)
    return sorted(h for h in hits if os.path.isfile(h))


def _find_tool(name):
    env = os.environ.get("SYNCTOOL_" + name.upper())
    if env and os.path.isfile(env):
        return env
    p = which(name)
    if p:
        return p
    for base in _GUESSES:
        for cand in (os.path.join(base, name + EXE), os.path.join(base, name)):
            if os.path.isfile(cand) and os.access(cand, os.X_OK if not IS_WIN else os.F_OK):
                return cand
    hits = _package_manager_dirs(name)
    return hits[0] if hits else None


FFPROBE = _find_tool("ffprobe")
FFMPEG = _find_tool("ffmpeg")


def rescan_tools():
    """Re-detect after an install, without restarting the app."""
    global FFPROBE, FFMPEG
    FFPROBE = _find_tool("ffprobe")
    FFMPEG = _find_tool("ffmpeg")
    return tools_status()


def diagnose():
    """Work out *why* ffmpeg is missing and what the user should do about it."""
    d = {
        "os": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "on_path": {"ffmpeg": which("ffmpeg"), "ffprobe": which("ffprobe")},
        "env_override": {k: os.environ.get("SYNCTOOL_" + k.upper())
                         for k in ("ffmpeg", "ffprobe")},
        "searched": [],
        "package_manager_copies": {"ffmpeg": _package_manager_dirs("ffmpeg")[:3],
                                   "ffprobe": _package_manager_dirs("ffprobe")[:3]},
        "platform": "windows" if IS_WIN else "mac" if IS_MAC else "linux",
        "installers": {m: which(m) for m in
                       (("winget", "choco", "scoop") if IS_WIN
                        else ("brew", "port") if IS_MAC
                        else ("apt-get", "dnf", "snap"))},
        "resolved": {"ffmpeg": FFMPEG, "ffprobe": FFPROBE},
    }
    for base in _WIN_GUESSES:
        d["searched"].append({"dir": base, "exists": os.path.isdir(base)})

    if FFMPEG and FFPROBE:
        d["cause"] = "ffmpeg is available - you're good."
        d["fix"] = None
    elif d["package_manager_copies"]["ffmpeg"]:
        d["cause"] = ("ffmpeg IS installed but was not on this process's PATH. "
                      "It has now been located directly.")
        d["fix"] = "rescan"
    else:
        avail = [m for m, p in d["installers"].items() if p]
        if avail:
            pretty = {"winget": "Windows Package Manager (winget)", "choco": "Chocolatey",
                      "scoop": "Scoop", "brew": "Homebrew", "port": "MacPorts",
                      "apt-get": "apt", "dnf": "dnf", "snap": "snap"}
            d["cause"] = f"ffmpeg is not installed. {pretty.get(avail[0], avail[0])} is available."
            d["fix"] = avail[0]
        else:
            d["cause"] = "ffmpeg is not installed and no package manager was found."
            d["fix"] = "manual"
            if IS_MAC:
                d["hint"] = ('Install Homebrew from https://brew.sh then run '
                             '"brew install ffmpeg", or download a static build from '
                             'https://evermeet.cx/ffmpeg/ and put ffmpeg and ffprobe '
                             'in /usr/local/bin.')
    return d


def install_ffmpeg(method="winget", log=None):
    """Install ffmpeg using the OS package manager. Returns (ok, transcript).

    Only ever shells out to a package manager the user already has - it does not
    download or execute arbitrary binaries.
    """
    def say(s):
        if log:
            log(s)
    cmds = {
        "winget": [which("winget") or "winget", "install", "--id", "Gyan.FFmpeg",
                   "-e", "--source", "winget",
                   "--accept-package-agreements", "--accept-source-agreements"],
        "choco": [which("choco") or "choco", "install", "ffmpeg", "-y"],
        "scoop": [which("scoop") or "scoop", "install", "ffmpeg"],
        "brew": [which("brew") or "brew", "install", "ffmpeg"],
        "port": [which("port") or "port", "install", "ffmpeg"],
        "snap": [which("snap") or "snap", "install", "ffmpeg"],
    }
    if method not in cmds:
        return False, f"Unknown install method: {method}"
    if not which(method):
        return False, f"{method} is not available on this machine."
    say("$ " + " ".join(cmds[method]) + "\n")
    try:
        p = subprocess.run(cmds[method], capture_output=True, text=True, timeout=900)
    except Exception as e:
        return False, f"Install failed to start: {e}"
    out = (p.stdout or "") + (p.stderr or "")
    say(out)
    st = rescan_tools()
    if st["ok"]:
        say("\nffmpeg found at: %s\n" % st["ffmpeg"])
        return True, out
    say("\nInstall finished but ffmpeg still isn't visible to this process.\n"
        "Restart the Sync Tool (PATH is only re-read on start).\n")
    return False, out


def tools_status():
    """Report whether ffmpeg is usable, with a version string when it is."""
    ok = bool(FFPROBE and FFMPEG)
    ver = None
    if ok:
        try:
            out = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True).stdout
            ver = out.splitlines()[0] if out else None
        except Exception:
            ok = False
    return {"ok": ok, "ffmpeg": FFMPEG, "ffprobe": FFPROBE, "version": ver}


class FFmpegMissing(RuntimeError):
    pass


# ----------------------------------------------------------------- probing --
def _run(cmd):
    """Run a tool, turning 'not installed' into a clear error instead of
    a WinError 2 traceback from deep inside subprocess."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise FFmpegMissing(
            "ffmpeg/ffprobe not found. Install ffmpeg 6.1+ and make sure it is on "
            "your PATH, or set the SYNCTOOL_FFMPEG and SYNCTOOL_FFPROBE "
            "environment variables to the full paths of the executables."
        )


def ffprobe(path):
    if not FFPROBE:
        raise FFmpegMissing(
            "ffprobe not found. Install ffmpeg 6.1+ (see README), then restart the tool."
        )
    r = _run([FFPROBE, "-v", "quiet", "-print_format", "json",
              "-show_format", "-show_streams", path])
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def _parse_created(fmt_tags):
    """Camera MP4s store UTC creation_time; BWF stores local date+time."""
    ct = fmt_tags.get("creation_time")
    if ct:
        try:
            return datetime.datetime.fromisoformat(ct.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    d, t = fmt_tags.get("date"), fmt_tags.get("creation_time")
    if d and t and len(t) == 8:
        try:
            return datetime.datetime.strptime(d + " " + t, "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return None


def media_info(path):
    """Return a dict describing one media file, or None if unreadable."""
    d = ffprobe(path)
    if not d or "format" not in d:
        return None
    fmt = d["format"]
    tags = fmt.get("tags", {})
    streams = d.get("streams", [])
    vid = [s for s in streams if s.get("codec_type") == "video"]
    aud = [s for s in streams if s.get("codec_type") == "audio"]
    fps = None
    if vid:
        try:
            n, den = vid[0].get("r_frame_rate", "0/1").split("/")
            fps = round(int(n) / int(den), 3)
        except Exception:
            pass
    created = _parse_created(tags)
    # seconds-since-midnight is all we need as a common axis
    start_s = None
    if created:
        start_s = created.hour * 3600 + created.minute * 60 + created.second
    return {
        "path": path,
        "name": os.path.basename(path),
        "kind": "video" if vid else "audio",
        "dur": float(fmt.get("duration") or 0),
        "fps": fps,
        "created": created.isoformat() if created else None,
        "date": created.date().isoformat() if created else None,
        "start_s": start_s,
        "n_audio": len(aud),
        "audio_codecs": [s.get("codec_tag_string") or s.get("codec_name") for s in aud],
        "timecode": tags.get("timecode"),
        "tc_s": tc_to_seconds(tags.get("timecode"), fps),
    }


def tc_to_seconds(tc, fps):
    """'HH:MM:SS:FF' -> seconds, using the clip's own frame rate.

    Frame counts are per-clip: a 50p camera writes frames 0-49, so the rate has
    to come from the file rather than the timeline.
    """
    if not tc or not fps:
        return None
    parts = str(tc).replace(";", ":").split(":")
    if len(parts) != 4:
        return None
    try:
        h, m, s, f = (int(x) for x in parts)
    except ValueError:
        return None
    return h * 3600 + m * 60 + s + (f / float(fps))


def scan(folder, recursive=True):
    """Probe every media file under a folder."""
    out = []
    for root, _dirs, files in os.walk(folder):
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in VIDEO_EXT and ext not in AUDIO_EXT:
                continue
            info = media_info(os.path.join(root, fn))
            if info:
                out.append(info)
        if not recursive:
            break
    return out


# ------------------------------------------------------- audio extraction --
def _cache_file(cache_dir, path, stream, rate, tag):
    key = hashlib.md5(f"{path}|{stream}|{rate}|{tag}".encode()).hexdigest()[:16]
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{key}.npy")


def _decode_pcm(path, stream, rate, max_seconds=None):
    """Decode one audio stream to mono float at `rate`.

    Falls back to raw packet extraction when ffmpeg can demux but not decode
    the codec (Canon 'ipcm' on ffmpeg < 6.1 is the case that motivated this).
    `max_seconds` limits how much is read - used for quick channel previews.
    """
    tmp = os.path.join(CACHE_DIR, f"_tmp{stream}.wav")
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not FFMPEG:
        raise FFmpegMissing("ffmpeg not found. Install ffmpeg 6.1+ (see README).")
    lim = ["-t", str(max_seconds)] if max_seconds else []
    r = _run([FFMPEG, "-y", "-loglevel", "error", "-i", path,
              "-map", f"0:a:{stream}"] + lim + ["-ac", "1", "-ar", str(rate),
              "-c:a", "pcm_s16le", tmp])
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 44:
        b = np.fromfile(tmp, np.uint8)
        i = b[:4000].tobytes().find(b"data")
        d = b[i + 8:]
        d = d[: len(d) // 2 * 2]
        x = d.view("<i2").astype(np.float64)
        os.remove(tmp)
        return x, rate

    # --- fallback: copy raw packets and infer the sample format -------------
    raw = os.path.join(CACHE_DIR, f"_tmp{stream}.raw")
    r = _run([FFMPEG, "-y", "-loglevel", "error", "-i", path,
              "-map", f"0:a:{stream}"] + lim + ["-c:a", "copy", "-f", "data", raw])
    if r.returncode != 0 or not os.path.exists(raw):
        return None, None
    info = media_info(path)
    dur = info["dur"] if info else 0
    if max_seconds:
        dur = min(dur, max_seconds)
    size = os.path.getsize(raw)
    src_rate = 48000
    x = None
    for bits in (24, 16, 32):
        expect = dur * src_rate * (bits // 8)
        if expect and abs(size / expect - 1) < 0.02:
            b = np.fromfile(raw, np.uint8)
            if bits == 24:
                n = (len(b) // 3) * 3
                b = b[:n].reshape(-1, 3).astype(np.int32)
                v = (b[:, 0] << 16) | (b[:, 1] << 8) | b[:, 2]      # big-endian
                x = np.where(v & 0x800000, v - (1 << 24), v).astype(np.float64)
            elif bits == 16:
                x = np.frombuffer(b.tobytes(), ">i2").astype(np.float64)
            else:
                x = np.frombuffer(b.tobytes(), ">i4").astype(np.float64)
            break
    os.remove(raw)
    if x is None:
        return None, None
    if rate != src_rate:                       # decimate with boxcar anti-alias
        f = src_rate // rate
        m = len(x) // f * f
        x = x[:m].reshape(-1, f).mean(1)
    return x, rate


def _movavg(a, w):
    c = np.concatenate([[0], np.cumsum(a)])
    n = len(a)
    lo = np.maximum(np.arange(n) - w + 1, 0)
    hi = np.arange(1, n + 1)
    return (c[hi] - c[lo]) / (hi - lo)


def _feature(x, fs):
    """High-passed log-RMS envelope at FR Hz - robust across different mics."""
    hop = max(1, fs // FR)
    n = len(x) // hop
    if n < 4:
        return np.zeros(4)
    xr = x[: n * hop].reshape(n, hop)
    e = np.log(np.sqrt((xr * xr).mean(1) + 1e-9) + 1e-6)
    return e - _movavg(e, int(1.5 * FR))


def envelope(path, stream=0, cache_dir=CACHE_DIR):
    f = _cache_file(cache_dir, path, stream, FR, "env")
    if os.path.exists(f):
        return np.load(f)
    x, fs = _decode_pcm(path, stream, 1000)
    e = _feature(x, fs) if x is not None else np.zeros(4)
    np.save(f, e)
    return e


def fine_signal(path, stream=0, cache_dir=CACHE_DIR):
    f = _cache_file(cache_dir, path, stream, FINE_FS, "fine")
    if os.path.exists(f):
        return np.load(f)
    x, _ = _decode_pcm(path, stream, FINE_FS)
    x = x if x is not None else np.zeros(4)
    np.save(f, x)
    return x


# ---------------------------------------------------------- correlation ----
def ncc(short, long):
    """Normalised cross-correlation of a zero-mean template over a signal.
    Returns array indexed by the template's start offset within `long`."""
    s = short - short.mean()
    ls, ll = len(s), len(long)
    if ls < 20 or ls >= ll:
        return None
    n = 1
    while n < ls + ll:
        n <<= 1
    num = np.fft.irfft(np.fft.rfft(long, n) * np.conj(np.fft.rfft(s, n)), n)[: ll - ls + 1]
    c2 = np.concatenate([[0], np.cumsum(long * long)])
    win = c2[ls: ll + 1] - c2[: ll - ls + 1]
    den = np.sqrt(np.maximum(win, 1e-9)) * np.sqrt((s * s).sum())
    return num / np.maximum(den, 1e-9)


def _peak(arr, lo=None, hi=None):
    if arr is None or not len(arr):
        return None, 0.0, 0.0
    lo = 0 if lo is None else max(0, int(lo))
    hi = len(arr) - 1 if hi is None else min(len(arr) - 1, int(hi))
    if hi < lo:
        return None, 0.0, 0.0
    seg = arr[lo:hi + 1]
    k = lo + int(np.argmax(seg))
    pk = float(arr[k])
    mask = np.ones(len(arr), bool)
    mask[max(0, k - 4): k + 5] = False
    second = float(np.max(arr[mask])) if mask.any() else 0.0
    return k, pk, second


# --------------------------------------------------------------- syncing ---
class Reference:
    """An audio recording used as the timing truth (e.g. a lav ISO)."""
    def __init__(self, info, stream=0):
        self.info = info
        self.name = info["name"]
        self.start = info["start_s"] or 0
        self.dur = info["dur"]
        self.stream = stream
        self._env = None

    @property
    def env(self):
        if self._env is None:
            self._env = envelope(self.info["path"], self.stream)
        return self._env


def build_bed(refs):
    """Sum all reference envelopes onto a common seconds-of-day axis."""
    if not refs:
        return 0, np.zeros(4)
    t0 = min(r.start for r in refs)
    end = max(r.start + len(r.env) / FR for r in refs)
    bed = np.zeros(int((end - t0) * FR) + FR)
    for r in refs:
        i = int((r.start - t0) * FR)
        e = r.env
        bed[i:i + len(e)] += e
    return t0, bed


def estimate_device_offset(clips, refs, streams, sample=12, min_peak=0.45):
    """Wide search of a few clips against the summed bed -> constant clock error.
    This is what catches a camera whose clock is hours wrong."""
    t0, bed = build_bed(refs)
    step = max(1, len(clips) // sample)
    deltas = []
    for c in clips[::step][:sample]:
        best = 0.0, None
        for st in streams:
            e = envelope(c["path"], st)
            k, pk, _sd = _peak(ncc(e, bed))
            if k is not None and pk > best[0]:
                best = pk, t0 + k / FR
        pk, pos = best
        if pos is not None and pk >= min_peak and c["start_s"] is not None:
            deltas.append(pos - c["start_s"])
    if not deltas:
        return 0.0, 0
    d = np.array(deltas)
    keep = d[np.abs(d - np.median(d)) < 5]      # a clock error is CONSTANT
    if not len(keep):
        keep = d
    return float(np.median(keep)), len(keep)


def match_clip(clip, refs, streams, centre, window, fine=True):
    """Windowed match against each individual reference; optional fine refine."""
    best = None
    for r in refs:
        if r.start > centre + clip["dur"] + window or r.start + r.dur < centre - window:
            continue
        for st in streams:
            e = envelope(clip["path"], st)
            if len(e) < 20:
                continue
            if len(e) <= len(r.env):
                arr = ncc(e, r.env)
                lo = (centre - window - r.start) * FR
                hi = (centre + window - r.start) * FR
                k, pk, sd = _peak(arr, lo, hi)
                pos = r.start + k / FR if k is not None else None
            else:
                arr = ncc(r.env, e)
                lo = (r.start - (centre + window)) * FR
                hi = (r.start - (centre - window)) * FR
                k, pk, sd = _peak(arr, lo, hi)
                pos = r.start - k / FR if k is not None else None
            if pos is not None and (best is None or pk > best["peak"]):
                best = {"ref": r.name, "peak": pk, "second": sd, "pos": pos, "stream": st}
    if not best:
        return None
    if fine and best["peak"] >= 0.45:
        ref = next((r for r in refs if r.name == best["ref"]), None)
        if ref is not None:
            fpos, fpk, fsd = _fine_refine(clip, best, ref)
            if fpos is not None and fpk >= 0.60 and fsd <= 0.75 * fpk:
                best.update(pos=fpos, fine_peak=fpk, locked=True)
                return best
    best["locked"] = False
    return best


def _fine_refine(clip, best, ref, search=1.2, seg=45):
    """Sample-level correlation around the coarse position."""
    cam = fine_signal(clip["path"], best["stream"])
    mic = fine_signal(ref.info["path"], ref.stream)
    if len(cam) < FINE_FS or len(mic) < FINE_FS:
        return None, 0, 1
    lag = best["pos"] - ref.start
    L, S, W = int(lag * FINE_FS), int(seg * FINE_FS), int(search * FINE_FS)
    c = cam[:S]
    lo, hi = max(0, L - W), min(len(mic), L + len(c) + W)
    m = mic[lo:hi]
    arr = ncc(c, m)
    k, pk, sd = _peak(arr)
    if k is None:
        return None, 0, 1
    return ref.start + (lo + k) / FINE_FS, pk, sd


def sync(clips, refs, streams, window=240, progress=None):
    """Full pipeline. Returns one result dict per clip."""
    offset, n = estimate_device_offset(clips, refs, streams)
    results = []
    for i, c in enumerate(clips):
        if progress:
            progress(i, len(clips), c["name"])
        centre = (c["start_s"] or 0) + offset
        m = match_clip(c, refs, streams, centre, window)
        if m and m.get("locked"):
            pos, status, conf = m["pos"], "locked", m.get("fine_peak", m["peak"])
        elif m and m["peak"] >= 0.55:
            pos, status, conf = centre, "approx", m["peak"]
        else:
            pos, status, conf = centre, "fallback", (m or {}).get("peak", 0.0)
        results.append({
            "name": c["name"], "path": c["path"], "dur": c["dur"], "fps": c["fps"],
            "created_s": c["start_s"], "position_s": round(pos, 3),
            "status": status, "confidence": round(float(conf), 3),
            "matched_ref": (m or {}).get("ref"), "device_offset_s": round(offset, 2),
        })
    return {"device_offset_s": round(offset, 2), "offset_samples": n, "clips": results}


def channel_preview(path, seconds=60, points=360):
    """Analyse every audio channel of one file so the user can choose which
    channels to sync on. Returns level, a sparkline, and duplicate detection.

    This exists because picking the wrong channels is the single biggest cause
    of bad sync: a camera may carry a clean wireless feed on ch1-2 and a useless
    on-board scratch mic on ch3-4.
    """
    info = media_info(path)
    if not info:
        return []
    chans = []
    for st in range(info["n_audio"]):
        x, fs = _decode_pcm(path, st, 4000, max_seconds=seconds)
        if x is None or not len(x):
            chans.append({"index": st, "ok": False, "level_db": None,
                          "wave": [], "duplicate_of": None, "silent": True})
            continue
        a = np.abs(x)
        rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
        n = min(points, max(1, len(a)))
        step = max(1, len(a) // n)
        env = a[: step * n].reshape(n, step).max(1)
        m = float(env.max()) or 1.0
        chans.append({"index": st, "ok": True, "rms": rms,
                      "wave": [round(float(v / m), 3) for v in env],
                      "duplicate_of": None, "silent": False,
                      "_sig": (x[: 4000 * 10]).astype(np.float64)})

    live = [c for c in chans if c.get("ok")]
    ref = max((c["rms"] for c in live), default=0.0) or 1.0
    for c in live:
        # cast to plain Python types - numpy scalars are not JSON serialisable
        c["level_db"] = float(round(20 * np.log10(max(c["rms"], 1e-9) / ref), 1))
        c["silent"] = bool(c["level_db"] < -45)     # effectively dead channel

    # duplicate detection - identical channels are a strong "ignore me" hint
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if b["duplicate_of"] is not None:
                continue
            x, y = a["_sig"], b["_sig"]
            n = min(len(x), len(y))
            if n < 1000:
                continue
            xa, yb = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
            den = (np.sqrt((xa * xa).sum()) * np.sqrt((yb * yb).sum())) or 1.0
            if float((xa * yb).sum() / den) > 0.99:
                b["duplicate_of"] = a["index"]
    for c in chans:
        c.pop("_sig", None)
        c.pop("rms", None)

    # suggest: loudest non-silent, non-duplicate channels
    picks = [c["index"] for c in chans
             if c.get("ok") and not c["silent"] and c["duplicate_of"] is None]
    return {"channels": chans, "suggested": picks[:2] or [0], "file": os.path.basename(path)}


def preview_wav(path, stream=0, start=0.0, dur=12.0, rate=22050):
    """Return a short WAV excerpt (bytes) of one channel, for listening in the
    browser. Falls back to the raw-PCM path for codecs ffmpeg can't decode."""
    import io, wave as wavemod
    if not FFMPEG:
        raise FFmpegMissing("ffmpeg not found.")
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = os.path.join(CACHE_DIR, f"_prev{stream}.wav")
    r = _run([FFMPEG, "-y", "-loglevel", "error", "-ss", str(max(0.0, start)),
              "-i", path, "-map", f"0:a:{stream}", "-t", str(dur),
              "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", tmp])
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 44:
        with open(tmp, "rb") as f:
            data = f.read()
        os.remove(tmp)
        return data

    # fallback: decode via the raw path, slice the window, build a WAV in memory
    x, fs = _decode_pcm(path, stream, rate, max_seconds=start + dur + 1)
    if x is None:
        raise RuntimeError("Could not decode audio for preview.")
    a = x[int(start * fs): int((start + dur) * fs)]
    if not len(a):
        raise RuntimeError("Preview window is past the end of the file.")
    peak = float(np.max(np.abs(a))) or 1.0
    pcm = (a / peak * 32000).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wavemod.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm)
    return buf.getvalue()


def tc_mapping(synced_clips):
    """Learn the relationship between embedded timecode and timeline position
    from clips that were already placed by waveform.

    Returns (delta, spread, n_used). `delta` is what to add to a clip's timecode
    to get its timeline position. `spread` (seconds) says how consistent that
    relationship is: a small spread means the camera's timecode is genuinely
    locked to the placement; a large one means it is free-running and useless
    for linking another camera.
    """
    deltas = []
    for c in synced_clips:
        tc = c.get("tc_s")
        # only trust clips whose own placement is solid
        if tc is None or c.get("status") not in ("locked", "manual"):
            continue
        deltas.append(c["position_s"] - tc)
    if not deltas:
        # fall back to any placed clip with a timecode
        deltas = [c["position_s"] - c["tc_s"] for c in synced_clips
                  if c.get("tc_s") is not None]
    if not deltas:
        return None, None, 0
    a = np.array(deltas, dtype=float)
    med = float(np.median(a))
    inl = a[np.abs(a - med) < 2.0]          # a jammed camera agrees within frames
    if len(inl) < 2:
        inl = a
    return float(np.median(inl)), float(inl.std()), int(len(inl))


def place_by_timecode(clips, delta, spread=None):
    """Position clips from their own embedded timecode using a learned delta."""
    out = []
    for c in clips:
        tc = c.get("tc_s")
        if tc is None:
            out.append({
                "name": c["name"], "path": c["path"], "dur": c["dur"], "fps": c["fps"],
                "created_s": c.get("start_s"), "position_s": round(c.get("start_s") or 0, 3),
                "status": "fallback", "confidence": 0.0, "matched_ref": None,
                "device_offset_s": 0, "note": "no timecode in this file",
            })
            continue
        out.append({
            "name": c["name"], "path": c["path"], "dur": c["dur"], "fps": c["fps"],
            "created_s": c.get("start_s"), "position_s": round(tc + delta, 3),
            "status": "timecode",
            "confidence": round(max(0.0, 1.0 - (spread or 0)), 3) if spread is not None else 1.0,
            "matched_ref": None, "device_offset_s": round(delta, 3),
            "tc": c.get("timecode"),
        })
    return out


def hhmmss(x):
    x = int(round(x))
    return f"{x//3600:02d}:{x%3600//60:02d}:{x%60:02d}"
