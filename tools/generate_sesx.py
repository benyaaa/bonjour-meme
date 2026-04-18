#!/usr/bin/env python3.12
"""
generate_sesx.py — Generate an Adobe Audition multitrack session from metadata.json.

Usage:
    python3.12 tools/generate_sesx.py [--output BonjourMeme.sesx] [--metadata metadata.json]

Reads metadata.json, skips entries with excluded=true, and generates a .sesx
file with one audio track per voicemail, clips placed sequentially on the timeline.
"""

import sys
if sys.version_info < (3, 8):
    sys.exit("Python 3.8+ required.")

import argparse
import json
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SESSION_SR = 44100           # session sample rate (Audition conforms 8kHz sources)
GAP_SAMPLES = 44100          # 1-second gap between clips


# ── XML helpers ───────────────────────────────────────────────────────────────

def sub(parent: ET.Element, tag: str, attrib: dict | None = None, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag, attrib or {})
    if text is not None:
        el.text = text
    return el


def fader_mute_panner(parent: ET.Element) -> None:
    """Add the standard Fader + Mute + StereoPanner component trio."""
    fader = sub(parent, "component", {
        "componentID": "Audition.Fader", "id": "trackFader", "name": "volume", "powered": "true"
    })
    sub(fader, "parameter", {"index": "0", "name": "volume", "parameterValue": "1"})
    sub(fader, "parameter", {"index": "1", "name": "static gain", "parameterValue": "1"})

    mute = sub(parent, "component", {
        "componentID": "Audition.Mute", "id": "trackMute", "name": "Mute", "powered": "true"
    })
    sub(mute, "parameter", {"index": "0", "parameterValue": "0"})
    sub(mute, "parameter", {"index": "1", "name": "mute", "parameterValue": "0"})

    pan = sub(parent, "component", {
        "componentID": "Audition.StereoPanner", "id": "trackPan", "name": "StereoPanner", "powered": "true"
    })
    sub(pan, "parameter", {"index": "0", "name": "Pan", "parameterValue": "0"})


def clip_fader_mute_panner(parent: ET.Element) -> None:
    """Same trio but with clip-level IDs."""
    fader = sub(parent, "component", {
        "componentID": "Audition.Fader", "id": "clipGain", "name": "volume", "powered": "true"
    })
    sub(fader, "parameter", {"index": "0", "name": "volume", "parameterValue": "1"})
    sub(fader, "parameter", {"index": "1", "name": "static gain", "parameterValue": "1"})

    mute = sub(parent, "component", {
        "componentID": "Audition.Mute", "id": "clipMute", "name": "Mute", "powered": "true"
    })
    sub(mute, "parameter", {"index": "0", "parameterValue": "0"})
    sub(mute, "parameter", {"index": "1", "name": "mute", "parameterValue": "0"})

    pan = sub(parent, "component", {
        "componentID": "Audition.StereoPanner", "id": "clipPan", "name": "StereoPanner", "powered": "true"
    })
    sub(pan, "parameter", {"index": "0", "name": "Pan", "parameterValue": "0"})


# ── Session builder ───────────────────────────────────────────────────────────

def build_sesx(entries: list[dict]) -> ET.Element:
    cursor = 0
    clips_data = []

    for i, entry in enumerate(entries):
        dur_samples = int(entry["duration_seconds"] * SESSION_SR)
        clips_data.append({
            "index": i,
            "file_id": i,
            "track_id": 10001 + i,
            "name": f"{entry.get('caller', 'Unknown')} — {entry.get('datetime', '')[:10]}",
            "start": cursor,
            "end": cursor + dur_samples,
            "dur": dur_samples,
        })
        cursor += dur_samples + GAP_SAMPLES

    total_duration = cursor
    num_tracks = len(entries)

    # Root
    root = ET.Element("sesx", {"version": "1.0"})
    root.text = "\n  "

    # <session>
    session = sub(root, "session", {
        "appVersion": "26.0",
        "audioChannelType": "stereo",
        "bitDepth": "24",
        "duration": str(total_duration),
        "sampleRate": str(SESSION_SR),
    })

    tracks = sub(session, "tracks")

    # One audioTrack per included voicemail
    for clip in clips_data:
        track = sub(tracks, "audioTrack", {
            "automationLaneOpenState": "false",
            "id": str(clip["track_id"]),
            "index": str(clip["index"]),
            "select": "false",
            "visible": "true",
        })

        params = sub(track, "trackParameters", {"trackHeight": "82"})
        sub(params, "name", text=clip["name"])

        audio_params = sub(track, "trackAudioParameters", {
            "audioChannelType": "mono",
            "automationMode": "1",
            "monitoring": "false",
            "recordArmed": "false",
            "solo": "false",
            "soloSafe": "false",
        })
        sub(audio_params, "trackOutput", {"outputID": "10000", "type": "trackID"})
        sub(audio_params, "trackInput", {"inputID": "1"})
        fader_mute_panner(audio_params)

        sub(track, "editParameter", {"parameterIndex": "0", "slotIndex": "4294967280"})

        # audioClip
        audio_clip = sub(track, "audioClip", {
            "clipAutoCrossfade": "true",
            "crossFadeHeadClipID": "-1",
            "crossFadeTailClipID": "-1",
            "endPoint": str(clip["end"]),
            "fileID": str(clip["file_id"]),
            "hue": "-1",
            "id": "0",
            "lockedInTime": "false",
            "looped": "false",
            "name": clip["name"],
            "offline": "false",
            "select": "false",
            "sourceInPoint": "0",
            "sourceOutPoint": str(clip["dur"]),
            "startPoint": str(clip["start"]),
            "zOrder": "10",
        })
        clip_fader_mute_panner(audio_clip)
        sub(audio_clip, "fadeIn", {
            "crossFadeLinkType": "linkedAsymmetric",
            "endPoint": "0", "shape": "19", "startPoint": "0", "type": "log",
        })
        sub(audio_clip, "fadeOut", {
            "crossFadeLinkType": "linkedAsymmetric",
            "endPoint": str(clip["dur"]), "shape": "19",
            "startPoint": str(clip["dur"]), "type": "log",
        })
        sub(audio_clip, "editParameter", {"parameterIndex": "0", "slotIndex": "4294967280"})
        channel_map = sub(audio_clip, "channelMap")
        sub(channel_map, "channel", {"index": "0", "sourceIndex": "0"})

    # masterTrack — always last, id=10000
    master = sub(tracks, "masterTrack", {
        "automationLaneOpenState": "false",
        "id": "10000",
        "index": str(num_tracks),
        "select": "false",
        "visible": "true",
    })
    master_params = sub(master, "trackParameters", {"trackHeight": "134"})
    sub(master_params, "name", text="Mix")
    master_audio = sub(master, "trackAudioParameters", {
        "audioChannelType": "stereo",
        "automationMode": "1",
        "monitoring": "false",
        "recordArmed": "false",
        "solo": "false",
        "soloSafe": "true",
    })
    sub(master_audio, "trackOutput", {"outputID": "1", "type": "hardwareOutput"})
    sub(master_audio, "trackInput", {"inputID": "-1"})
    fader_mute_panner(master_audio)
    sub(master, "editParameter", {"parameterIndex": "0", "slotIndex": "4294967280"})

    # <sessionState>
    state = sub(session, "sessionState", {"ctiPosition": "0", "smpteStart": "0"})
    sub(state, "selectionState", {"selectionDuration": "0", "selectionStart": "0"})
    sub(state, "viewState", {
        "horizontalViewDuration": str(total_duration),
        "horizontalViewStart": "0",
        "trackControlsWidth": "224",
        "verticalScrollOffset": "0",
    })
    sub(state, "timeFormatState", {
        "beatsPerBar": "4",
        "beatsPerMinute": "120.00",
        "timeFormat": "timeFormatDecimal",
    })

    sub(session, "clipGroups")

    # <files>
    files_el = sub(root, "files")
    for clip, entry in zip(clips_data, entries):
        sub(files_el, "file", {
            "absolutePath": entry["absolute_path"],
            "id": str(clip["file_id"]),
            "mediaHandler": "AmioMpeg4",
            "recoveryID": str(uuid.uuid4()).upper(),
        })

    # <audioDevice> — minimal, uses system defaults
    audio_device = sub(root, "audioDevice", {
        "inputID": "22222222-2222-2222-2222-222222222222",
        "outputID": "11111111-1111-1111-1111-111111111111",
    })
    sub(audio_device, "inputPort", {"id": "1", "name": "Default Stereo Input"})
    sub(audio_device, "outputPort", {"id": "1", "name": "Default Output"})
    sub(audio_device, "outputPort", {"id": "4", "name": "Default Stereo Output"})

    return root


def pretty_xml(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(raw)
    lines = dom.toprettyxml(indent="  ", encoding=None).splitlines()
    # minidom adds a redundant <?xml?> declaration; replace with the correct one
    result_lines = ['<?xml version="1.0" encoding="UTF-8" standalone="no" ?>', "<!DOCTYPE sesx>"]
    result_lines += [l for l in lines if not l.startswith("<?xml")]
    return "\n".join(result_lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Adobe Audition .sesx from metadata.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "BonjourMeme.sesx",
        help="Output .sesx file path (default: BonjourMeme.sesx)",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=PROJECT_ROOT / "metadata.json",
        help="Path to metadata.json (default: metadata.json)",
    )
    args = parser.parse_args()

    if not args.metadata.exists():
        print(f"Error: {args.metadata} not found. Run process.py first.", file=sys.stderr)
        sys.exit(1)

    with open(args.metadata, "r", encoding="utf-8") as f:
        all_entries = json.load(f)

    included = [e for e in all_entries if not e.get("excluded")]
    excluded_count = len(all_entries) - len(included)

    print(
        f"Including {len(included)} / {len(all_entries)} voicemails "
        f"({excluded_count} excluded)."
    )

    if not included:
        print("Nothing to include — all entries are excluded. Aborting.")
        sys.exit(1)

    # Check that processed files exist
    missing = [e for e in included if not Path(e["absolute_path"]).exists()]
    if missing:
        print("Warning: the following files are missing from disk:")
        for e in missing:
            print(f"  {e['absolute_path']}")
        print("They will still appear in the session but may be offline in Audition.")

    root = build_sesx(included)
    xml_str = pretty_xml(root)

    args.output.write_text(xml_str, encoding="utf-8")
    print(f"Session written to: {args.output}")
    print(f"Open with: open \"{args.output}\"")


if __name__ == "__main__":
    main()
