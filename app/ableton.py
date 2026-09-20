"""
Ableton-Live-Vorlagenprojekt fuer "In DAW oeffnen".

Ableton Live importiert eine per `open -a`/Apple-Event uebergebene Audiodatei
NICHT in ein bereits offenes Set (anders als Logic/Cubase) -- es wird nur das
Fenster nach vorne geholt. Es gibt kein AppleScript-Dictionary/CLI, ueber das
sich ein Import erzwingen liesse.

Workaround: `.als` ist gzip-komprimiertes XML. Aus einer vom Nutzer selbst in
Ableton gebauten Vorlage (eigene Spur 1 mit gewuenschter FX-Kette, aber ohne
Clip dort -- z.B. Reverb + Delay) wird bei jedem Aufruf frisch ein
temporaeres Projekt erzeugt: der Clip landet im ARRANGEMENT FENSTER der
ersten Spur (nicht in der Session-Ansicht -- die beiden sind in der .als-XML
zwei komplett getrennte Strukturen, siehe unten), das Ergebnis wird neu
gzip-komprimiert und per `open -a` geoeffnet -- ein normaler Projekt-Load,
kein Sonderverhalten wie beim `openFile`-Event.

Der Clip-Block ist bewusst NICHT aus der Nutzer-Vorlage gelesen (die hat ja
keinen), sondern ein hier hardcodiertes Skelett, abgeleitet aus einem echten,
von Hand in Ableton 12 per Drag&Drop INS ARRANGEMENT FENSTER erzeugten
Referenzprojekt (Diff gegen dieselbe Datei ohne Clip zeigt exakt, was
Ableton selbst dafuer schreibt -- keine Vermutung). Die Arrangement-Clips
einer Spur liegen unter deren <MainSequencer><Sample><ArrangerAutomation>
<Events>, komplett getrennt von <MainSequencer><ClipSlotList> (das ist die
Session-Ansicht -- ein frueherer Versuch setzte den Clip faelschlich dort
ein, der Track erschien dann im Session-Grid statt im Arrangement). Anders
als beim manuellen Ableton-Import sind Loop/Warp-Marker NICHT beat-genau --
IsWarped bleibt aus, der Clip liegt einfach in voller Laenge unbearbeitet
auf Spur 1. Ziel ist ein hoerbar geladener Track, kein fertiges Beatgrid.
"""
from __future__ import annotations

import gzip
import os
import re
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

_ARRANGEMENT_EVENTS_RE = re.compile(
    r'(<Sample>\s*<ArrangerAutomation>\s*)<Events\s*/>',
    re.DOTALL,
)

# Jede Spur -- auch Return-/Master-/PreHear-Spuren, die nie eine eigene
# Track-Id haben -- besitzt ihr eigenes <Sample><ArrangerAutomation>
# <Events>. Ohne Eingrenzung auf den Abschnitt von Spur 1 wuerde eine
# bereits belegte Spur 1 NICHT auffallen: der Regex faende einfach den
# naechsten freien Events-Block irgendeiner anderen Spur (z.B. einer
# Return-Spur) und der Clip laende dort statt auf Spur 1. Alle Spurarten
# zaehlen hier als Grenze, nur AudioTrack ist als ERSTE Spur zulaessig.
_TRACK_TAG_RE = re.compile(
    r'<(AudioTrack|MidiTrack|GroupTrack|ReturnTrack|MasterTrack|PreHearTrack)\b')

# Jede Spur haelt NEBEN ihrer echten DeviceChain eine zweite, strukturell
# identische Kopie fuer den eingefrorenen Zustand (<FreezeSequencer> direkt
# nach <MainSequencer>, beide mit eigenem <Sample><ArrangerAutomation>
# <Events>). Ohne diese Abgrenzung faende der Events-Regex bei einer
# belegten Spur 1 einfach den freien Freeze-Slot und haette es faelschlich
# fuer "frei" gehalten (an echtem Material bestaetigt).
_MAIN_SEQUENCER_RE = re.compile(r'<MainSequencer>')
_FREEZE_SEQUENCER_RE = re.compile(r'<FreezeSequencer>')

# Feldwerte an einer echten, per Drag&Drop ins Arrangement-Fenster in
# Ableton 12.3 erzeugten Referenz geprueft (Diff derselben Datei mit/ohne
# Clip; Log zeigt zusaetzlich fehlerfreies Laden + Warp-Analyse).
_CLIP_TEMPLATE = """<AudioClip Id="1" Time="0">
<LomId Value="0" />
<LomIdView Value="0" />
<CurrentStart Value="0" />
<CurrentEnd Value="{beats}" />
<Loop>
<LoopStart Value="0" />
<LoopEnd Value="{beats}" />
<StartRelative Value="0" />
<LoopOn Value="false" />
<OutMarker Value="{beats}" />
<HiddenLoopStart Value="0" />
<HiddenLoopEnd Value="{beats}" />
</Loop>
<Name Value="{name}" />
<Annotation Value="" />
<Color Value="2" />
<LaunchMode Value="0" />
<LaunchQuantisation Value="0" />
<TimeSignature>
<TimeSignatures>
<RemoteableTimeSignature Id="0">
<Numerator Value="4" />
<Denominator Value="4" />
<Time Value="0" />
</RemoteableTimeSignature>
</TimeSignatures>
</TimeSignature>
<Envelopes>
<Envelopes />
</Envelopes>
<ScrollerTimePreserver>
<LeftTime Value="0" />
<RightTime Value="0" />
</ScrollerTimePreserver>
<TimeSelection>
<AnchorTime Value="0" />
<OtherTime Value="0" />
</TimeSelection>
<Legato Value="false" />
<Ram Value="false" />
<GrooveSettings>
<GrooveId Value="-1" />
</GrooveSettings>
<Disabled Value="false" />
<VelocityAmount Value="0" />
<FollowAction>
<FollowTime Value="4" />
<IsLinked Value="true" />
<LoopIterations Value="1" />
<FollowActionA Value="4" />
<FollowActionB Value="0" />
<FollowChanceA Value="100" />
<FollowChanceB Value="0" />
<JumpIndexA Value="1" />
<JumpIndexB Value="1" />
<FollowActionEnabled Value="false" />
</FollowAction>
<Grid>
<FixedNumerator Value="1" />
<FixedDenominator Value="16" />
<GridIntervalPixel Value="20" />
<Ntoles Value="2" />
<SnapToGrid Value="true" />
<Fixed Value="false" />
</Grid>
<FreezeStart Value="0" />
<FreezeEnd Value="0" />
<IsWarped Value="false" />
<TakeId Value="1" />
<IsInKey Value="true" />
<ScaleInformation>
<Root Value="0" />
<Name Value="0" />
</ScaleInformation>
<AutomationEnvelopesListWrapper LomId="0" />
<SampleRef>
<FileRef>
<RelativePathType Value="0" />
<RelativePath Value="" />
<Path Value="{path}" />
<Type Value="2" />
<LivePackName Value="" />
<LivePackId Value="" />
<OriginalFileSize Value="{size}" />
<OriginalCrc Value="0" />
<SourceHint Value="" />
</FileRef>
<LastModDate Value="{mtime}" />
<SourceContext />
<SampleUsageHint Value="0" />
<DefaultDuration Value="{duration_samples}" />
<DefaultSampleRate Value="{sample_rate}" />
<SamplesToAutoWarp Value="1" />
</SampleRef>
<Onsets>
<UserOnsets />
<HasUserOnsets Value="false" />
</Onsets>
<WarpMode Value="0" />
<GranularityTones Value="30" />
<GranularityTexture Value="65" />
<FluctuationTexture Value="25" />
<TransientResolution Value="6" />
<TransientLoopMode Value="2" />
<TransientEnvelope Value="100" />
<ComplexProFormants Value="100" />
<ComplexProEnvelope Value="128" />
<Sync Value="true" />
<HiQ Value="true" />
<Fade Value="true" />
<Fades>
<FadeInLength Value="0" />
<FadeOutLength Value="0" />
<ClipFadesAreInitialized Value="true" />
<CrossfadeInState Value="0" />
<FadeInCurveSkew Value="0" />
<FadeInCurveSlope Value="0" />
<FadeOutCurveSkew Value="0" />
<FadeOutCurveSlope Value="0" />
<IsDefaultFadeIn Value="true" />
<IsDefaultFadeOut Value="true" />
</Fades>
<PitchCoarse Value="0" />
<PitchFine Value="0" />
<SampleVolume Value="1" />
<WarpMarkers>
<WarpMarker Id="0" SecTime="0" BeatTime="0" />
</WarpMarkers>
<SavedWarpMarkersForStretched />
<MarkersGenerated Value="false" />
<IsSongTempoLeader Value="false" />
</AudioClip>"""


class AbletonTemplateError(RuntimeError):
    """Vorlage fehlt, ist keine gueltige .als-Datei oder hat keine freie Spur 1."""


def is_ableton(app_path: str | None) -> bool:
    """Grobe Ja/Nein-Erkennung anhand des vom Nutzer gewaehlten App-Namens."""
    return "ableton" in Path(app_path or "").stem.lower()


def _load_template_xml(template_path: str) -> str:
    """Vorlage gzip-entpacken, wirft AbletonTemplateError bei jedem Problem."""
    template = Path(template_path)
    if not template.is_file():
        raise AbletonTemplateError(
            f"Ableton-Vorlage nicht gefunden: {template_path}")
    try:
        with gzip.open(template, "rb") as f:
            return f.read().decode("utf-8")
    except OSError as exc:
        raise AbletonTemplateError(
            f"Ableton-Vorlage nicht lesbar (keine gueltige .als-Datei?): {exc}") from exc


def _track1_main_sequencer_span(xml: str) -> tuple[int, int]:
    """
    Start/Ende der ECHTEN (nicht der Freeze-Kopie) DeviceChain von Spur 1 --
    das ist der einzige Ort, an dem ein Arrangement-Clip auf Spur 1 zaehlt.
    """
    tracks = list(_TRACK_TAG_RE.finditer(xml))
    if not tracks:
        raise AbletonTemplateError("Ableton-Vorlage enthaelt keine Spur.")
    first = tracks[0]
    if first.group(1) != "AudioTrack":
        raise AbletonTemplateError(
            "Spur 1 der Ableton-Vorlage ist keine Audiospur.")
    track_end = tracks[1].start() if len(tracks) > 1 else len(xml)
    track_xml = xml[first.start():track_end]

    main = _MAIN_SEQUENCER_RE.search(track_xml)
    if not main:
        raise AbletonTemplateError(
            "Ableton-Vorlage: unerwartete Struktur auf Spur 1 (kein MainSequencer).")
    freeze = _FREEZE_SEQUENCER_RE.search(track_xml, main.end())
    seq_end = freeze.start() if freeze else len(track_xml)
    return first.start() + main.end(), first.start() + seq_end


def validate_template(template_path: str) -> None:
    """
    Prueft nur die Struktur (keine Zieldatei noetig) -- fuer sofortiges
    Feedback beim Speichern in den Einstellungen statt erst beim ersten
    Oeffnen eines Tracks. Wirft AbletonTemplateError, wenn Spur 1 im
    Arrangement-Fenster nicht frei ist (z.B. eine eigene, bereits belegte
    Projektdatei statt einer eigens dafuer gebauten Vorlage).
    """
    xml = _load_template_xml(template_path)
    start, end = _track1_main_sequencer_span(xml)
    if not _ARRANGEMENT_EVENTS_RE.search(xml[start:end]):
        raise AbletonTemplateError(
            "Ableton-Vorlage hat keine leere Spur 1 im Arrangement-Fenster "
            "(erste <Events /> unter ArrangerAutomation dort muss frei sein).")


def build_project(track_path: str, template_path: str) -> bytes:
    """Vorlage laden, Zieldatei als Arrangement-Clip auf Spur 1 einsetzen, neu komprimieren."""
    xml = _load_template_xml(template_path)

    from . import probe as probe_mod

    res = probe_mod.probe(track_path)
    if not res.ok:
        raise AbletonTemplateError(f"Datei nicht lesbar: {res.error or track_path}")

    # Ableton loest einen relativen Path nicht auf ("Datei konnte nicht
    # geoeffnet werden") -- immer absolut schreiben, auch wenn der Aufrufer
    # (versehentlich) einen relativen Pfad uebergibt.
    abs_path = str(Path(track_path).resolve())

    duration_samples = round(res.duration_s * res.sample_rate)
    # Beats sind bei IsWarped=false nur die Anzeige-Laenge (4/4, 120 BPM
    # angenommen) -- ohne Bezug zum tatsaechlichen Tempo der Datei.
    beats = round(res.duration_s * 120 / 60, 6)
    name = res.title or Path(track_path).stem

    clip = _CLIP_TEMPLATE.format(
        beats=beats,
        name=escape(name),
        path=escape(abs_path),
        size=os.path.getsize(track_path),
        mtime=int(os.path.getmtime(track_path)),
        duration_samples=duration_samples,
        sample_rate=res.sample_rate,
    )

    start, end = _track1_main_sequencer_span(xml)
    track1_xml, count = _ARRANGEMENT_EVENTS_RE.subn(
        lambda m: m.group(1) + f"<Events>{clip}</Events>", xml[start:end], count=1)
    if count != 1:
        raise AbletonTemplateError(
            "Ableton-Vorlage hat keine leere Spur 1 im Arrangement-Fenster "
            "(erste <Events /> unter ArrangerAutomation dort muss frei sein).")
    new_xml = xml[:start] + track1_xml + xml[end:]

    return gzip.compress(new_xml.encode("utf-8"))


def build_temp_project(track_path: str, template_path: str) -> str:
    """Wie build_project(), schreibt das Ergebnis aber in eine temporaere .als."""
    data = build_project(track_path, template_path)
    fd, out_path = tempfile.mkstemp(
        prefix=f"tracktab-{int(time.time())}-", suffix=".als")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return out_path
