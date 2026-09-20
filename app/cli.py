"""
Kommandozeile.

  scan       Bibliothek analysieren (inkrementell, mit Cache)
  check      Einzelne Dateien pruefen und Details ausgeben
  calibrate  Schwellwerte an selbst erzeugten Transcodes pruefen
  report     HTML/CSV/M3U aus der Datenbank erzeugen
  serve      Report ausliefern, Player, Drag & Drop, Einstellungen
  reclassify   Nach geaenderten Grenzen neu bewerten (ohne Neuanalyse)
  recheck-tags Tags neu lesen, Auffaelligkeiten neu bewerten (ohne Neuanalyse)
  stats        Zusammenfassung aus der Datenbank
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn,
    TimeElapsedColumn, TimeRemainingColumn,
)
from rich.table import Table

from . import backup as backup_mod
from . import classify as classify_mod
from . import config as cfgmod
from . import db as db_mod
from . import jobs
from . import loudness as loudness_mod
from . import taganomaly as taganomaly_mod
from .analyzer import analyse_file

console = Console()

_VERDICT_STYLE = {
    classify_mod.VERDICT_FAKE: "bold red",
    classify_mod.VERDICT_SUSPECT: "yellow",
    classify_mod.VERDICT_OK: "green",
    classify_mod.VERDICT_UNKNOWN: "dim",
}


def _progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


# ── scan ──────────────────────────────────────────────────────────────────

def cmd_scan(args) -> int:
    cfg = cfgmod.load()

    if args.covers:
        if not cfg.get("external_music"):
            console.print("[yellow]Hinweis:[/yellow] Keine Music App in den "
                          "Einstellungen ausgewählt — der Abgleich bleibt "
                          "wirkungslos, solange die Music.app-Integration "
                          "dort aus ist.")
        elif not cfg.get("cover_auto_fill", True):
            console.print("[yellow]Hinweis:[/yellow] „Cover aus Music.app "
                          "übernehmen“ ist in den Einstellungen deaktiviert "
                          "— der Abgleich läuft trotzdem, da er hier explizit "
                          "angefordert wurde.")
        console.print("[bold]Cover-Abgleich mit Music.app …[/bold]")
        result = jobs.run_scan(cfg, cover_scan=True)
        console.print(f"{result['covers_filled']} Cover ergänzt, "
                      f"{result['covers_updated']} mit Music.app abgeglichen "
                      f"(geändert/entfernt).")
        return 0

    if args.limit:
        console.print(f"[dim]Begrenzt auf {args.limit} Dateien[/dim]")
    console.print("[bold]Bibliothek wird durchsucht …[/bold]")

    conn = db_mod.connect(cfg)
    workers = cfgmod.worker_count()
    state = {"task": None}

    with _progress() as progress:
        def on_progress(done, total, phase):
            if phase == "analysieren" and state["task"] is None and total:
                console.print(f"{total:,} Dateien zu analysieren "
                              f"[dim]· {workers} parallele Prozesse[/dim]\n")
                state["task"] = progress.add_task("Analyse", total=total)
            if state["task"] is not None:
                progress.update(state["task"], completed=done)

        result = jobs.run_scan(
            cfg,
            paths=args.path or None,
            force=args.force,
            limit=args.limit,
            prune=args.prune,
            on_progress=on_progress,
        )

    if not result["found"]:
        console.print("[red]Keine passenden Dateien gefunden.[/red] "
                      "Prüfe library_paths in config.yaml.")
        return 1

    skipped = result["found"] - result["todo"]
    if skipped and not args.limit:
        console.print(f"[dim]{skipped:,} unverändert aus dem Cache[/dim]")
    if result.get("renamed"):
        console.print(f"[dim]{result['renamed']} nur umbenannte Dateien "
                      f"(Gross-/Kleinschreibung) erkannt und zusammengeführt[/dim]")
    if result["removed"]:
        console.print(f"[dim]{result['removed']} verschwundene Dateien "
                      f"aus der DB entfernt[/dim]")
    if result.get("covers_filled"):
        console.print(f"[dim]{result['covers_filled']} Cover automatisch "
                      f"aus Music.app ergänzt[/dim]")
    if result.get("covers_updated"):
        console.print(f"[dim]{result['covers_updated']} Cover mit Music.app "
                      f"abgeglichen (geändert/entfernt)[/dim]")

    console.print()
    _print_summary(conn)
    conn.close()
    console.print(
        "\n[dim]Nächster Schritt:[/dim] "
        "[bold]./run.command report[/bold] erzeugt den HTML-Report."
    )
    return 0


# ── check ─────────────────────────────────────────────────────────────────

def cmd_check(args) -> int:
    cfg = cfgmod.load()
    for path in args.files:
        p = Path(path).expanduser()
        if not p.exists():
            console.print(f"[red]Nicht gefunden:[/red] {p}")
            continue
        st = p.stat()
        row = analyse_file((str(p), st.st_size, st.st_mtime, cfg))
        _print_detail(row)
    return 0


def _print_detail(row: dict) -> None:
    style = _VERDICT_STYLE.get(row["verdict"], "")
    console.print(f"\n[bold]{Path(row['path']).name}[/bold]")
    if row["artist"] or row["title"]:
        console.print(f"[dim]{row['artist']} — {row['title']}[/dim]")

    is_lossless = row.get("codec_family") == "lossless"

    t = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    t.add_row("Verdikt", f"[{style}]{row['verdict']}[/{style}]  "
                         f"(Konfidenz {row['confidence']:.2f})")
    t.add_row("Codec", f"{row.get('codec') or '?'} "
                       f"({'verlustfrei' if is_lossless else 'verlustbehaftet'})")
    if is_lossless:
        t.add_row("PCM-Datenrate", f"{row['declared_kbps']} kbps · "
                                   f"{row['sample_rate']} Hz · {row['channels']} ch "
                                   "[dim](keine Qualitätsangabe)[/dim]")
        t.add_row("Gemessen", f"Cutoff {row['cutoff_hz']/1000:.2f} kHz")
    else:
        t.add_row("Deklariert", f"{row['declared_kbps']} kbps "
                                f"{row['bitrate_mode'] or ''} · "
                                f"{row['sample_rate']} Hz · {row['channels']} ch")
        t.add_row("Gemessen", f"Cutoff {row['cutoff_hz']/1000:.2f} kHz "
                              f"→ Klasse {row['measured_kbps']} kbps")
    t.add_row("Flanke", f"{row['steepness_db']:.1f} dB"
                        + ("  [bold]Brickwall[/bold]" if row["is_brickwall"] else ""))
    if row["lame_lowpass_hz"]:
        t.add_row("LAME-Header", f"{row['encoder']} · Lowpass "
                                 f"{row['lame_lowpass_hz']/1000:.1f} kHz")
    elif row["encoder"]:
        t.add_row("Encoder", row["encoder"])
    t.add_row("Blöcke", f"{row['gated_blocks']} von {row['total_blocks']} ausgewertet")
    if row.get("integrated_lufs"):
        cfg = cfgmod.load()
        clip = loudness_mod.clip_risk(row["true_peak_dbtp"], cfg)
        hint = loudness_mod.club_hint(row["integrated_lufs"], cfg)
        t.add_row("Lautheit", f"{row['integrated_lufs']:.1f} LUFS · "
                              f"True Peak {row['true_peak_dbtp']:.1f} dBTP · "
                              f"LRA {row['lra_lu']:.1f} LU"
                              + ("  [bold red]Clip-Risiko[/bold red]" if clip else ""))
        t.add_row("", f"[dim]{hint} — Hinweis, kein Urteil[/dim]")
    console.print(t)
    for reason in row["reasons"]:
        console.print(f"  [dim]·[/dim] {reason}")
    if row["error"]:
        console.print(f"  [red]Fehler:[/red] {row['error']}")


# ── stats ─────────────────────────────────────────────────────────────────

def _print_summary(conn) -> None:
    s = db_mod.summary(conn)
    table = Table(title=f"Ergebnis · {s['total']:,} Dateien in der Datenbank")
    table.add_column("Verdikt")
    table.add_column("Dateien", justify="right")
    table.add_column("Anteil", justify="right")
    order = [classify_mod.VERDICT_FAKE, classify_mod.VERDICT_SUSPECT,
             classify_mod.VERDICT_OK, classify_mod.VERDICT_UNKNOWN]
    for verdict in order:
        count = s["by_verdict"].get(verdict, 0)
        if not count:
            continue
        style = _VERDICT_STYLE.get(verdict, "")
        pct = 100.0 * count / max(s["total"], 1)
        table.add_row(f"[{style}]{verdict}[/{style}]", f"{count:,}", f"{pct:.1f} %")
    console.print(table)
    if s["errors"]:
        console.print(f"[dim]{s['errors']} Dateien konnten nicht analysiert werden[/dim]")


def cmd_stats(args) -> int:
    conn = db_mod.connect(cfgmod.load())
    _print_summary(conn)
    return 0


# ── report ────────────────────────────────────────────────────────────────

def cmd_report(args) -> int:
    from . import report as report_mod
    cfg = cfgmod.load()
    conn = db_mod.connect(cfg)
    if db_mod.summary(conn)["total"] == 0:
        console.print("[red]Datenbank ist leer.[/red] Zuerst 'scan' ausführen.")
        return 1
    paths = report_mod.build_all(conn, cfg)
    console.print("[bold green]Report erzeugt:[/bold green]")
    for label, path in paths.items():
        console.print(f"  {label:<6} {path}")
    ign = db_mod.summary(conn)["ignored"]
    if ign:
        console.print(f"[dim]{ign} ausgeblendete Tracks bleiben in allen "
                      f"Ausgaben unberücksichtigt.[/dim]")
    console.print("\n[dim]Öffnen mit:[/dim] [bold]./run.command serve[/bold]"
                  "  (nur so lassen sich Tracks dauerhaft ausblenden)")
    return 0


# ── serve ─────────────────────────────────────────────────────────────────

def cmd_serve(args) -> int:
    from . import server as server_mod
    return server_mod.serve(console, port=args.port, open_browser=not args.no_open)


def cmd_reclassify(args) -> int:
    """Neubewertung aus gespeicherten Messwerten — ohne erneutes Dekodieren."""
    from . import report as report_mod
    cfg = cfgmod.load()
    conn = db_mod.connect(cfg)
    stats = classify_mod.reclassify_all(conn, cfg)
    console.print(f"{stats['total']:,} Dateien neu eingeordnet · "
                  f"[bold]{stats['changed']:,}[/bold] mit geändertem Verdikt")
    report_mod.build_all(conn, cfg)
    _print_summary(conn)
    return 0


# ── recheck-tags ──────────────────────────────────────────────────────────

def cmd_recheck_tags(args) -> int:
    """Tags aller bekannten Dateien frisch lesen und tag_issues neu bewerten
    -- ohne ffprobe/Spektralanalyse, deutlich billiger als 'scan --force'."""
    from . import report as report_mod
    cfg = cfgmod.load()
    conn = db_mod.connect(cfg)
    stats = taganomaly_mod.recheck_all(conn, cfg)
    console.print(f"{stats['total']:,} Dateien geprüft · "
                  f"[bold]{stats['with_issues']:,}[/bold] mit Auffälligkeiten")
    if stats["per_code"]:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Auffälligkeit")
        table.add_column("Anzahl", justify="right")
        for code, count in sorted(stats["per_code"].items(), key=lambda kv: -kv[1]):
            table.add_row(code, f"{count:,}")
        console.print(table)
    report_mod.build_all(conn, cfg)
    return 0


# ── calibrate ─────────────────────────────────────────────────────────────

def cmd_calibrate(args) -> int:
    from . import calibrate as calib_mod
    return calib_mod.run(console, args)


# ── backup ────────────────────────────────────────────────────────────────

def cmd_backup(args) -> int:
    cfg = cfgmod.load()
    if args.backup_action == "create":
        path = backup_mod.create_backup(cfg)
        backup_mod.prune_backups(cfg)
        console.print(f"[bold green]Backup erstellt:[/bold green] {path}")
        return 0

    if args.backup_action == "list":
        backups = backup_mod.list_backups(cfg)
        if not backups:
            console.print("[dim]Noch keine automatischen Backups.[/dim]")
            return 0
        table = Table()
        table.add_column("Datei")
        table.add_column("Größe", justify="right")
        for p in backups:
            table.add_row(p.name, f"{p.stat().st_size/1048576:.1f} MB")
        console.print(table)
        console.print(f"[dim]{backup_mod.backup_dir(cfg)}[/dim]")
        return 0

    if args.backup_action == "restore":
        source = Path(args.file).expanduser()
        try:
            safety = backup_mod.restore_backup(source, cfg)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            return 1
        console.print(f"[bold green]Wiederhergestellt aus:[/bold green] {source}")
        if safety:
            console.print(f"[dim]Vorheriger Stand gesichert unter: {safety}[/dim]")
        from . import report as report_mod
        conn = db_mod.connect(cfg)
        try:
            report_mod.build_all(conn, cfg)
        finally:
            conn.close()
        console.print("[dim]Report neu erzeugt.[/dim]")
        return 0

    return 1


# ── main ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tracktab",
        description="Prüft, ob MP3s ihre deklarierte Bitrate wirklich enthalten.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="Bibliothek analysieren")
    s.add_argument("path", nargs="*", help="Ordner/Dateien statt library_paths")
    s.add_argument("--force", action="store_true", help="Cache ignorieren")
    s.add_argument("--limit", type=int, default=0, help="Nur N Dateien (Testlauf)")
    s.add_argument("--prune", action="store_true",
                   help="DB-Einträge zu gelöschten Dateien entfernen")
    s.add_argument("--covers", action="store_true",
                   help="Nur Cover-Abgleich mit Music.app für ALLE bekannten "
                        "Dateien ohne Cover (kein Neu-Scan der Audiodateien)")
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("check", help="Einzelne Dateien im Detail prüfen")
    c.add_argument("files", nargs="+")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("report", help="HTML/CSV/M3U erzeugen")
    r.set_defaults(func=cmd_report)

    sv = sub.add_parser(
        "serve",
        help="Report im Browser öffnen — nur so bleibt Ausblenden gespeichert",
    )
    sv.add_argument("--port", type=int, default=8756)
    sv.add_argument("--no-open", action="store_true", help="Browser nicht öffnen")
    sv.set_defaults(func=cmd_serve)

    rc = sub.add_parser(
        "reclassify",
        help="Nach geänderten Klassengrenzen neu bewerten (ohne Neuanalyse)")
    rc.set_defaults(func=cmd_reclassify)

    rt = sub.add_parser(
        "recheck-tags",
        help="Tags neu lesen und Auffälligkeiten neu bewerten (ohne Neuanalyse)")
    rt.set_defaults(func=cmd_recheck_tags)

    st = sub.add_parser("stats", help="Zusammenfassung anzeigen")
    st.set_defaults(func=cmd_stats)

    cal = sub.add_parser(
        "calibrate",
        help="Bekannte Transcodes erzeugen und die Erkennung daran prüfen",
    )
    cal.add_argument("sources", nargs="*", help="Referenzdateien (Default: aus der DB)")
    cal.add_argument("--count", type=int, default=6, help="Anzahl Referenztracks")
    cal.add_argument("--keep", action="store_true", help="Testdateien behalten")
    cal.set_defaults(func=cmd_calibrate)

    bk = sub.add_parser("backup", help="Datenbank-Backups erstellen/anzeigen/einspielen")
    bk_sub = bk.add_subparsers(dest="backup_action", required=True)
    bk_sub.add_parser("create", help="Backup jetzt erstellen")
    bk_sub.add_parser("list", help="Vorhandene automatische Backups anzeigen")
    bk_restore = bk_sub.add_parser("restore", help="Datenbank aus einem Backup wiederherstellen")
    bk_restore.add_argument("file", help="Pfad zur Backup-Datei (.db)")
    bk.set_defaults(func=cmd_backup)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        backup_mod.maybe_auto_backup(cfgmod.load())
    except Exception as exc:                       # noqa: BLE001
        console.print(f"[dim]Automatisches Backup übersprungen: {exc}[/dim]")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Abgebrochen.[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
