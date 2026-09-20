"""
Kalibrierung an bekannten Faellen.

Nimmt saubere 320er aus der eigenen Sammlung, kodiert sie kuenstlich auf
128/192/256 kbps herunter und anschliessend wieder auf 320 kbps hoch — also
genau der Transcode, den das Tool finden soll. Die Erkennung muss dabei den
Cutoff der ERSTEN Kodierung melden, nicht 320.

Damit sind die Schwellwerte an echtem Material geprueft statt an Lehrbuchwerten.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rich.table import Table

from . import classify as classify_mod
from . import config as cfgmod
from . import db as db_mod
from . import media
from .analyzer import analyse_file

_STAGES = [128, 192, 256]          # MP3: hoch auf 320
_STAGES_AAC = [96, 128, 192]       # AAC: hoch auf 256


def _encode(src: Path, dst: Path, kbps: int, codec: str = "libmp3lame") -> bool:
    cmd = [
        media.ffmpeg_path(), "-v", "error", "-nostdin", "-y",
        "-i", str(src),
        "-map", "0:a:0", "-c:a", codec, "-b:a", f"{kbps}k",
        str(dst),
    ]
    return subprocess.run(cmd, capture_output=True, timeout=300).returncode == 0


def _wrap_lossless(src: Path, dst: Path, codec: str) -> bool:
    """Eine Datei verlustfrei in einen anderen Container umpacken (kein
    Bitraten-Ziel, der Inhalt bleibt exakt erhalten) -- simuliert den Fall
    'Lossy-Quelle wurde nach FLAC/WAV exportiert'."""
    cmd = [
        media.ffmpeg_path(), "-v", "error", "-nostdin", "-y",
        "-i", str(src), "-map", "0:a:0", "-c:a", codec,
        str(dst),
    ]
    return subprocess.run(cmd, capture_output=True, timeout=300).returncode == 0


def _pick_sources(cfg: dict, count: int) -> list[str]:
    """Saubere Referenztracks aus der Datenbank: echte 320er mit hohem Cutoff."""
    conn = db_mod.connect(cfg)
    rows = conn.execute(
        """
        SELECT path FROM files
        WHERE status = 'ok' AND verdict = ? AND declared_kbps >= 315
              AND cutoff_hz >= 20000 AND duration_s BETWEEN 120 AND 420
        ORDER BY cutoff_hz DESC
        LIMIT ?
        """,
        (classify_mod.VERDICT_OK, count),
    ).fetchall()
    return [r["path"] for r in rows]


def _new_table(title: str) -> Table:
    table = Table(title=title)
    table.add_column("Track", overflow="ellipsis", max_width=28)
    table.add_column("Kette")
    table.add_column("Cutoff", justify="right")
    table.add_column("Flanke", justify="right")
    table.add_column("erkannt als", justify="right")
    table.add_column("Verdikt")
    table.add_column("", justify="center")
    return table


def _run_lossy_chain(console, sources: list[str], cfg: dict, work: Path, keep: bool,
                      *, label: str, stages: list[int], final_kbps: int,
                      codec: str, ext: str) -> tuple[int, int]:
    """Original -> je Zwischenstufe hochkodiert -> muss Cutoff der ERSTEN
    (schlechteren) Kodierung zeigen, nicht den der finalen."""
    console.print(f"[bold]{label}-Kette: Original → {'/'.join(map(str, stages))} kbps "
                  f"→ zurück auf {final_kbps} kbps[/bold]")
    table = _new_table(f"Erkennung an bekannten {label}-Transcodes")

    hits = 0
    total = 0
    for src in sources:
        src_path = Path(src)
        if not src_path.exists():
            console.print(f"[red]Fehlt:[/red] {src}")
            continue

        st = src_path.stat()
        base = analyse_file((str(src_path), st.st_size, st.st_mtime, cfg))
        table.add_row(
            src_path.stem, "Original (Quelle)",
            f"{base['cutoff_hz']/1000:.1f} kHz",
            f"{base['steepness_db']:.0f} dB",
            f"{base['measured_kbps']}", base["verdict"], "—",
        )

        stem = "".join(ch for ch in src_path.stem if ch.isalnum() or ch in " -_")[:40]
        for target in stages:
            total += 1
            low = work / f"{stem}__{label}{target}{ext}"
            up = work / f"{stem}__{label}{target}_to{final_kbps}{ext}"
            if not _encode(src_path, low, target, codec) or not _encode(low, up, final_kbps, codec):
                table.add_row("", f"→{target}→{final_kbps}", "—", "—", "—",
                              "[red]Encode fehlgeschlagen[/red]", "✗")
                continue

            ust = up.stat()
            res = analyse_file((str(up), ust.st_size, ust.st_mtime, cfg))
            correct = res["measured_kbps"] == target
            flagged = res["verdict"] in (classify_mod.VERDICT_FAKE,
                                         classify_mod.VERDICT_SUSPECT)
            if correct and flagged:
                hits += 1
            mark = "[green]✓[/green]" if (correct and flagged) else "[red]✗[/red]"
            table.add_row(
                "", f"→{target}→{final_kbps}",
                f"{res['cutoff_hz']/1000:.1f} kHz",
                f"{res['steepness_db']:.0f} dB",
                f"{res['measured_kbps']}",
                f"[{'red' if flagged else 'dim'}]{res['verdict']}[/]",
                mark,
            )
            low.unlink(missing_ok=True)
            if not keep:
                up.unlink(missing_ok=True)

    console.print(table)
    return hits, total


def _run_lossless_wrap(console, sources: list[str], cfg: dict, work: Path,
                        keep: bool) -> tuple[int, int]:
    """Das eigentliche Modus-B-Versprechen testen: eine leicht kodierte
    Lossy-Quelle verlustfrei nach FLAC/WAV verpacken -- die Kante muss dort
    weiterhin erkannt werden, obwohl der Container 'verlustfrei' ist."""
    console.print("[bold]Lossless-Rewrap: Quelle → 128 kbps MP3 → FLAC/WAV verpackt[/bold]")
    table = _new_table("Erkennung nach Lossless-Rewrap")

    hits = 0
    total = 0
    wraps = [("FLAC", ".flac", "flac"), ("WAV", ".wav", "pcm_s16le")]
    for src in sources:
        src_path = Path(src)
        if not src_path.exists():
            continue
        stem = "".join(ch for ch in src_path.stem if ch.isalnum() or ch in " -_")[:40]
        low = work / f"{stem}__wrap128.mp3"
        if not _encode(src_path, low, 128):
            table.add_row(src_path.stem, "→128→FLAC/WAV", "—", "—", "—",
                          "[red]Encode fehlgeschlagen[/red]", "✗")
            continue

        for label, ext, codec in wraps:
            total += 1
            wrapped = work / f"{stem}__wrap128{ext}"
            if not _wrap_lossless(low, wrapped, codec):
                table.add_row(src_path.stem, f"→128→{label}", "—", "—", "—",
                              "[red]Encode fehlgeschlagen[/red]", "✗")
                continue

            wst = wrapped.stat()
            res = analyse_file((str(wrapped), wst.st_size, wst.st_mtime, cfg))
            is_lossless = res["codec_family"] == "lossless"
            flagged = res["verdict"] in (classify_mod.VERDICT_FAKE,
                                         classify_mod.VERDICT_SUSPECT)
            if is_lossless and flagged:
                hits += 1
            mark = "[green]✓[/green]" if (is_lossless and flagged) else "[red]✗[/red]"
            table.add_row(
                src_path.stem, f"→128→{label}",
                # raw_cutoff_hz statt cutoff_hz: Modus B wertet die
                # ungefilterte Kantenposition, cutoff_hz faellt bei
                # niedriger Flanke (< cliff_min_db, MP3-Schwelle) auf
                # Nyquist zurueck und waere hier irrefuehrend.
                f"{res['raw_cutoff_hz']/1000:.1f} kHz",
                f"{res['steepness_db']:.0f} dB",
                res["codec_family"] or "?",
                f"[{'red' if flagged else 'dim'}]{res['verdict']}[/]",
                mark,
            )
            if not keep:
                wrapped.unlink(missing_ok=True)
        low.unlink(missing_ok=True)

    console.print(table)
    return hits, total


def _summarize(console, label: str, hits: int, total: int) -> float:
    rate = 100.0 * hits / max(total, 1)
    style = "green" if rate >= 90 else ("yellow" if rate >= 70 else "red")
    console.print(
        f"[{style}]{label}: {hits} von {total} korrekt erkannt ({rate:.0f} %)[/{style}]"
    )
    return rate


def run(console, args) -> int:
    cfg = cfgmod.load()
    work = cfgmod.resolve("data/calibration")
    work.mkdir(parents=True, exist_ok=True)

    sources = [str(Path(s).expanduser()) for s in args.sources]
    if not sources:
        sources = _pick_sources(cfg, args.count)
    if not sources:
        console.print(
            "[red]Keine Referenztracks gefunden.[/red] Entweder Dateien direkt "
            "angeben oder zuerst 'scan' laufen lassen."
        )
        return 1

    console.print(f"[bold]Kalibrierung mit {len(sources)} Referenztracks[/bold]\n")

    mp3_hits, mp3_total = _run_lossy_chain(
        console, sources, cfg, work, args.keep,
        label="MP3", stages=_STAGES, final_kbps=320,
        codec="libmp3lame", ext=".mp3",
    )
    console.print()

    aac_encoder = media.resolved_aac_encoder(cfg)
    aac_hits, aac_total = _run_lossy_chain(
        console, sources, cfg, work, args.keep,
        label="AAC", stages=_STAGES_AAC, final_kbps=256,
        codec=aac_encoder, ext=".m4a",
    )
    console.print()

    wrap_hits, wrap_total = _run_lossless_wrap(console, sources, cfg, work, args.keep)
    console.print()

    console.print(
        "[dim]Korrekt heißt bei MP3/AAC: richtige Bitrate-Klasse UND als "
        "auffällig markiert. Bei Lossless-Rewrap: als verlustfrei erkannt "
        "UND als auffällig markiert.[/dim]"
    )
    mp3_rate = _summarize(console, "MP3", mp3_hits, mp3_total)
    _summarize(console, "AAC", aac_hits, aac_total)
    _summarize(console, "Lossless-Rewrap", wrap_hits, wrap_total)

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    # Nur die MP3-Kette bestimmt den Exit-Code: sie ist die einzige mit
    # kalibrierten, verifizierten Schwellwerten. AAC/Lossless sind neu und
    # sollen nicht versehentlich CI/Automatisierung rot einfaerben, bis sie
    # an echtem Material nachjustiert wurden.
    return 0 if mp3_rate >= 70 else 1
