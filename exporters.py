"""
exporters.py - turn a sync result into an editable timeline.

FCP7 XML (xmeml v4) is the interchange format here. It is the most widely
understood timeline format there is: Premiere Pro, DaVinci Resolve, Final Cut 7
and Media Composer (via import) all read it. One file covers every NLE, and it
avoids driving Resolve through its scripting API.

Everything is non-destructive: the XML references your media in place.
"""

import os, urllib.request, urllib.parse
from xml.sax.saxutils import escape


def _pathurl(path):
    """file:// URL for a local path, correct on Windows and POSIX."""
    p = os.path.abspath(path)
    if os.name == "nt":
        return "file://localhost/" + urllib.request.pathname2url(p).lstrip("/")
    return "file://localhost" + urllib.parse.quote(p)


def _rate(fps, ntsc):
    return f"<rate><timebase>{fps}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def _is_ntsc(fps):
    return abs(fps - round(fps)) > 1e-6 or int(round(fps)) in (24, 30, 60, 120)


def build_fcp7_xml(rows, sequence_name="Synced Timeline", fps=25,
                   start_seconds=None, lead_seconds=3):
    """rows: dicts with kind, track, track_name, file, path, position_s, dur_s,
    fps, and optional scene/shot/angle/reel/comments/keywords/color.

    Returns the XML document as a string.
    """
    if not rows:
        raise ValueError("Nothing to export.")

    tb = int(round(fps))
    ntsc = _is_ntsc(fps)

    if start_seconds is None:
        start_seconds = max(0.0, min(float(r["position_s"]) for r in rows) - lead_seconds)
    origin = int(round(start_seconds * fps))

    vids = [r for r in rows if r["kind"] == "video"]
    auds = [r for r in rows if r["kind"] == "audio"]
    ntracks_v = max([int(r["track"]) for r in vids] or [0])
    ntracks_a = max([int(r["track"]) for r in auds] or [0])

    # one <file> per unique media path; later references use the id only
    file_ids, seen = {}, {}
    for i, r in enumerate(rows):
        if r["path"] not in seen:
            seen[r["path"]] = f"file-{len(seen)+1}"
        file_ids[id(r)] = seen[r["path"]]

    total = 0
    for r in rows:
        end = int(round(float(r["position_s"]) * fps)) - origin + \
              max(1, int(round(float(r["dur_s"]) * fps)))
        total = max(total, end)

    out = []
    A = out.append
    A('<?xml version="1.0" encoding="UTF-8"?>')
    A('<!DOCTYPE xmeml>')
    A('<xmeml version="4">')
    A(f'<sequence id="{escape(sequence_name)}">')
    A(f'<name>{escape(sequence_name)}</name>')
    A(f'<duration>{total}</duration>')
    A(_rate(tb, ntsc))
    A(f'<timecode>{_rate(tb, ntsc)}<string>{_tc(start_seconds, fps)}</string>'
      f'<frame>{origin}</frame><displayformat>NDF</displayformat></timecode>')
    A('<media>')

    emitted = set()

    def clipitem(r, idx, kind):
        """One clip on a track. `kind` is 'video' or 'audio'."""
        pos = int(round(float(r["position_s"]) * fps)) - origin
        length = max(1, int(round(float(r["dur_s"]) * fps)))
        fid = file_ids[id(r)]
        name = escape(r["file"])
        s = [f'<clipitem id="{fid}-{kind}-{idx}">',
             f'<name>{name}</name>',
             f'<duration>{length}</duration>',
             _rate(tb, ntsc),
             f'<start>{max(0,pos)}</start><end>{max(1,pos+length)}</end>',
             f'<in>0</in><out>{length}</out>',
             '<enabled>TRUE</enabled>']
        if fid in emitted:
            s.append(f'<file id="{fid}"/>')
        else:
            emitted.add(fid)
            clip_fps = float(r["fps"]) if r.get("fps") else fps
            ctb = int(round(clip_fps))
            s.append(f'<file id="{fid}"><name>{name}</name>'
                     f'<pathurl>{escape(_pathurl(r["path"]))}</pathurl>'
                     f'{_rate(ctb, _is_ntsc(clip_fps))}'
                     f'<duration>{max(1,int(round(float(r["dur_s"])*clip_fps)))}</duration>'
                     f'<media>'
                     f'{"<video><samplecharacteristics>"+_rate(ctb,_is_ntsc(clip_fps))+"</samplecharacteristics></video>" if r["kind"]=="video" else ""}'
                     f'<audio><samplecharacteristics><depth>16</depth>'
                     f'<samplerate>48000</samplerate></samplecharacteristics>'
                     f'<channelcount>1</channelcount></audio>'
                     f'</media></file>')
        if kind == "audio":
            s.append('<sourcetrack><mediatype>audio</mediatype>'
                     '<trackindex>1</trackindex></sourcetrack>')
        else:
            s.append('<sourcetrack><mediatype>video</mediatype>'
                     '<trackindex>1</trackindex></sourcetrack>')

        # metadata the NLE will show in its bins / columns
        notes = []
        for k, label in (("scene", "Scene"), ("shot", "Shot"), ("angle", "Angle"),
                         ("reel", "Reel"), ("keywords", "Keywords"),
                         ("comments", "Comments")):
            v = (r.get(k) or "").strip()
            if v:
                notes.append(f"{label}: {v}")
        conf = r.get("confidence")
        stat = r.get("status")
        if stat:
            notes.append(f"sync: {stat}" + (f" ({conf})" if conf not in (None, "") else ""))
        if notes:
            s.append(f'<comments><mastercomment1>{escape(" | ".join(notes))}'
                     f'</mastercomment1></comments>')
            s.append(f'<logginginfo><description>{escape(" | ".join(notes))}</description>'
                     f'<scene>{escape((r.get("scene") or ""))}</scene>'
                     f'<shottake>{escape((r.get("shot") or ""))}</shottake>'
                     f'</logginginfo>')
        col = (r.get("color") or "").strip()
        if col:
            s.append(f'<labels><label2>{escape(col)}</label2></labels>')
        s.append('</clipitem>')
        return "".join(s)

    # ---- video tracks ----
    A('<video>')
    A(f'<format><samplecharacteristics>{_rate(tb, ntsc)}'
      f'<width>1920</width><height>1080</height></samplecharacteristics></format>')
    for t in range(1, ntracks_v + 1):
        A('<track>')
        for i, r in enumerate([x for x in vids if int(x["track"]) == t]):
            A(clipitem(r, f"{t}-{i}", "video"))
        A('<enabled>TRUE</enabled><locked>FALSE</locked>')
        name = next((x.get("track_name") for x in vids if int(x["track"]) == t), None)
        if name:
            A(f'<outputchannelindex>{t}</outputchannelindex>')
        A('</track>')
    A('</video>')

    # ---- audio tracks ----
    if ntracks_a:
        A('<audio>')
        for t in range(1, ntracks_a + 1):
            A('<track>')
            for i, r in enumerate([x for x in auds if int(x["track"]) == t]):
                A(clipitem(r, f"{t}-{i}", "audio"))
            A('<enabled>TRUE</enabled><locked>FALSE</locked>')
            A(f'<outputchannelindex>{t}</outputchannelindex>')
            A('</track>')
        A('</audio>')

    A('</media>')
    A('</sequence>')
    A('</xmeml>')
    return "\n".join(out)


def _tc(seconds, fps):
    f = int(round(seconds * fps))
    tb = int(round(fps))
    s, fr = divmod(f, tb)
    return f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}:{fr:02d}"


def write_fcp7_xml(rows, path, **kw):
    xml = build_fcp7_xml(rows, **kw)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return path
