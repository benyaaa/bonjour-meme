#!/usr/bin/env python3.12
"""
process.py — Interactive voicemail metadata tool for BonjourMeme.

Usage:
    python3.12 tools/process.py <voicemails_dir> [--model base|small|medium] [--output-dir processed]

For each .m4a in the input folder:
  1. Auto-extracts existing tags and duration
  2. Plays the audio (non-blocking, via afplay)
  3. Transcribes locally using Whisper
  4. Prompts for caller, date, transcription, tags, and include/exclude decision
  5. Copies + renames the file to processed/ and writes metadata tags
  6. Appends the entry to metadata.json
"""

import sys
if sys.version_info < (3, 8):
    sys.exit("Python 3.8+ required. Run: /usr/local/bin/python3.12 tools/process.py ...")

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import questionary
from faster_whisper import WhisperModel
from mutagen.mp4 import MP4, MP4FreeForm, AtomDataType
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
METADATA_FILE = PROJECT_ROOT / "metadata.json"

PRESET_TAGS = ["intimate", "urgent", "emotional", "background", "key-scene", "ambient"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_metadata() -> list[dict]:
    if METADATA_FILE.exists():
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_metadata(entries: list[dict]) -> None:
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def already_processed(original_filename: str, entries: list[dict]) -> bool:
    return any(e["original_filename"] == original_filename for e in entries)


def extract_metadata(filepath: Path) -> tuple[float, str]:
    """Return (duration_seconds, creation_date_iso)."""
    audio = MP4(filepath)
    duration = audio.info.length
    tags = audio.tags or {}
    raw_date = tags.get("\xa9day", [None])[0]
    if raw_date:
        creation_date = raw_date
    else:
        mtime = os.path.getmtime(filepath)
        creation_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    return duration, creation_date


def safe_filename(date_str: str, caller: str, output_dir: Path) -> Path:
    """Build a collision-safe output path like 2024-01-15_Marie_Dupont.m4a"""
    base = f"{date_str}_{caller.replace(' ', '_')}"
    candidate = output_dir / f"{base}.m4a"
    counter = 2
    while candidate.exists():
        candidate = output_dir / f"{base}_{counter}.m4a"
        counter += 1
    return candidate


def write_tags(dest: Path, caller: str, date_str: str, transcription: str, tags: list[str]) -> None:
    audio = MP4(dest)
    audio["\xa9nam"] = [caller]
    audio["\xa9day"] = [date_str]
    audio["\xa9cmt"] = [transcription]
    audio["----:com.apple.iTunes:CALLER_TAGS"] = [
        MP4FreeForm(", ".join(tags).encode("utf-8"), AtomDataType.UTF8)
    ]
    audio.save()


def print_summary(entries: list[dict]) -> None:
    included = [e for e in entries if not e.get("excluded")]
    excluded = [e for e in entries if e.get("excluded")]
    table = Table(title="Processed voicemails", box=box.SIMPLE, show_lines=False)
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Caller", style="green")
    table.add_column("Date")
    table.add_column("Tags")
    table.add_column("Audition", justify="center")
    for e in entries:
        status = "[green]✓[/green]" if not e.get("excluded") else "[red]✗[/red]"
        table.add_row(
            e.get("processed_filename") or e["original_filename"],
            e.get("caller", "—"),
            e.get("datetime", "—"),
            ", ".join(e.get("tags", [])) or "—",
            status,
        )
    console.print(table)
    console.print(
        f"[bold]{len(entries)} total[/bold]  ·  "
        f"[green]{len(included)} included[/green]  ·  "
        f"[red]{len(excluded)} excluded[/red]\n"
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def process_file(
    filepath: Path,
    model: "WhisperModel",
    output_dir: Path,
    entries: list[dict],
) -> dict:
    filename = filepath.name

    # B — Auto-extract
    duration, creation_date = extract_metadata(filepath)
    duration_fmt = f"{int(duration // 60)}:{int(duration % 60):02d}"

    # C — Start playback (non-blocking)
    afplay_proc = subprocess.Popen(
        ["/usr/bin/afplay", str(filepath)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # D — Transcribe (faster-whisper returns a lazy generator of segments)
    with console.status("[bold yellow]Transcribing with Whisper…[/bold yellow]"):
        segments, _info = model.transcribe(str(filepath), beam_size=5, language="fr")
        transcription_auto = " ".join(seg.text for seg in segments).strip()

    # Display info panel
    console.print(
        Panel(
            f"[bold cyan]{filename}[/bold cyan]\n"
            f"[dim]Duration:[/dim] {duration_fmt}   "
            f"[dim]Detected date:[/dim] {creation_date}\n\n"
            f"[bold]Whisper transcription:[/bold]\n{transcription_auto}",
            title="[bold]Voicemail[/bold]",
            border_style="blue",
        )
    )

    # E — Interactive prompts
    include = questionary.confirm(
        "Include in Audition session?",
        default=True,
    ).ask()

    # Stop playback
    try:
        afplay_proc.terminate()
        afplay_proc.wait(timeout=2)
    except Exception:
        pass

    if not include:
        entry = {
            "original_filename": filename,
            "processed_filename": None,
            "absolute_path": None,
            "caller": None,
            "datetime": creation_date,
            "duration_seconds": round(duration, 3),
            "transcription": transcription_auto,
            "tags": [],
            "excluded": True,
            "processed_at": datetime.now().isoformat(),
        }
        console.print("[red]  Marked as excluded — will not appear in Audition session.[/red]\n")
        return entry

    caller_name = questionary.text("Caller name:").ask() or "Unknown"

    date_confirmed = questionary.text(
        "Date/time (YYYY-MM-DD HH:MM):",
        default=creation_date[:16],
    ).ask() or creation_date[:16]

    transcription_confirmed = questionary.text(
        "Transcription (edit if needed):",
        default=transcription_auto,
    ).ask() or transcription_auto

    selected_tags = questionary.checkbox(
        "Tags:",
        choices=PRESET_TAGS,
    ).ask() or []

    # F — Copy, rename, write tags
    date_str = date_confirmed[:10]  # YYYY-MM-DD
    dest = safe_filename(date_str, caller_name, output_dir)
    shutil.copy2(filepath, dest)
    write_tags(dest, caller_name, date_confirmed, transcription_confirmed, selected_tags)

    console.print(f"[green]  Saved → {dest.name}[/green]\n")

    return {
        "original_filename": filename,
        "processed_filename": dest.name,
        "absolute_path": str(dest),
        "caller": caller_name,
        "datetime": date_confirmed,
        "duration_seconds": round(duration, 3),
        "transcription": transcription_confirmed,
        "tags": selected_tags,
        "excluded": False,
        "processed_at": datetime.now().isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive voicemail metadata processor")
    parser.add_argument("voicemails_dir", type=Path, help="Folder containing .m4a voicemails")
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model to use (default: base, ~74MB)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "processed",
        help="Destination folder for renamed files (default: processed/)",
    )
    args = parser.parse_args()

    voicemails_dir: Path = args.voicemails_dir.resolve()
    output_dir: Path = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(voicemails_dir.glob("*.m4a"))
    if not files:
        console.print(f"[red]No .m4a files found in {voicemails_dir}[/red]")
        sys.exit(1)

    # A — Load Whisper model once
    with console.status(
        f"[bold yellow]Loading Whisper model '{args.model}' "
        f"(first run downloads the model from HuggingFace, ~150–500MB)…[/bold yellow]"
    ):
        model = WhisperModel(args.model, device="cpu", compute_type="int8")

    console.print(f"[green]Whisper model '{args.model}' ready.[/green]\n")

    entries = load_metadata()
    already_done = {e["original_filename"] for e in entries}

    new_files = [f for f in files if f.name not in already_done]
    skipped = len(files) - len(new_files)

    if skipped:
        console.print(f"[dim]Skipping {skipped} already-processed file(s).[/dim]\n")
    if not new_files:
        console.print("[bold green]All files already processed.[/bold green]")
        print_summary(entries)
        return

    console.print(f"[bold]Processing {len(new_files)} voicemail(s)…[/bold]\n")

    for i, filepath in enumerate(new_files, 1):
        console.rule(f"[bold blue]{i} / {len(new_files)}  ·  {filepath.name}[/bold blue]")
        entry = process_file(filepath, model, output_dir, entries)
        entries.append(entry)
        save_metadata(entries)

    console.rule("[bold green]Done[/bold green]")
    print_summary(entries)
    console.print(
        "[dim]Run [bold]python3.12 tools/generate_sesx.py[/bold] "
        "to create the Adobe Audition session.[/dim]"
    )


if __name__ == "__main__":
    main()
