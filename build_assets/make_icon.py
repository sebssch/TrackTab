#!/usr/bin/env python3
"""Erzeugt saemtliche TrackTab-Symbole aus einer einzigen Zeichenvorschrift.

Motiv: drei Karteireiter (gruen / blau / rot) ueber einer hellen Karte mit
sechs Wellenform-Balken -- Reiter fuer das Ordnen der Bibliothek, Wellenform
fuer das Material. Die Farben sind dieselben wie in der Oberflaeche
(app.css: --dc-green / --dc-blue / --dc-red), fuer das Symbol leicht
aufgehellt, damit sie auf dem dunklen Korpus stehen.

Aufruf (aus dem Projektverzeichnis):

    ./.venv/bin/python build_assets/make_icon.py

Geschrieben werden:
    build_assets/icon-1024.png      Vorschau in voller Groesse (gitignored)
    build_assets/appicon.iconset/   Zwischenstand fuer iconutil (gitignored)
    build_assets/appicon.icns       App-Symbol fuers Bundle (mp3qc.spec)
    build_assets/favicon-32.png     flaechenfuellende Fassung fuer die Web-UI
    build_assets/favicon-128.png    (gitignored, wandern als data:-URI in die
                                    Oberflaeche)
    docs/app-icon.png               Symbol fuer die README (256 px)

    app/webui/index.html            Favicon + Kopfzeilen-Symbol werden
                                    darin als data:-URI ersetzt
    app/pwa.py                      PWA-Icons (180/192/512 px) werden darin
                                    als reines Base64 ersetzt (ausgeliefert
                                    als echte Bilddatei, nicht als data:-URI
                                    -- Safari ignoriert data:-URI-Icons in
                                    Manifest/apple-touch-icon unzuverlaessig)
                                    -- aus der gepolsterten App-Zeichnung,
                                    nicht der randlosen Favicon-Fassung

Nach dem Lauf `./run.command report` ausfuehren: der Server liefert
data/report.html aus, nicht app/webui/ -- ohne den Neubau zeigt die
Oberflaeche weiter das alte Symbol. Das PWA-Manifest (app/pwa.py) braucht das
NICHT -- es wird live ausgeliefert, ein Serverneustart reicht.
"""
import base64
import math
import re
import pathlib
import subprocess
import sys
from io import BytesIO

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "build_assets"
DOCS = ROOT / "docs"

SS = 4                      # Supersampling: erst gross zeichnen, dann verkleinern
CANVAS = 1024               # Kantenlaenge der Zielgrafik
BODY_APP = 824              # macOS-Vorgabe: der Korpus fuellt das Raster nicht aus
BODY_FLAT = 1000            # fuer Favicon/Kopfzeile -- dort waere Rand vergeudet

# Farbwelt der Oberflaeche, leicht aufgehellt.
GREEN = (46, 209, 110)
BLUE = (74, 148, 255)
RED = (255, 92, 92)
CARD = (247, 249, 253)      # helle Karte
INKBAR = (38, 48, 70)       # Wellenform auf der Karte
BG_TOP = (36, 41, 58)       # Korpusverlauf oben links
BG_BOTTOM = (10, 12, 18)    # ... nach unten rechts

# Balkenhoehen der Wellenform. Sechs Stueck: wenige, dicke Balken bleiben
# bis hinunter zu 32 px lesbar -- bei zehn verschmieren sie zu einer Flaeche.
AMPS = (0.46, 0.86, 1.00, 0.62, 0.92, 0.56)
AMPS_SMALL = (0.55, 1.00, 0.70, 0.90)   # fuer 16/32 px, siehe render()


def superellipse(cx, cy, r, n=5.0, samples=1440):
    """Punktliste der abgerundeten Quadratform, die macOS fuer Symbole nutzt."""
    pts = []
    for i in range(samples):
        t = 2 * math.pi * i / samples
        ct, st = math.cos(t), math.sin(t)
        pts.append((cx + r * math.copysign(abs(ct) ** (2 / n), ct),
                    cy + r * math.copysign(abs(st) ** (2 / n), st)))
    return pts


def gradient(size, c0, c1, angle=58.0):
    """Linearer Farbverlauf ueber die ganze Flaeche."""
    yy, xx = np.mgrid[0:size, 0:size]
    proj = xx * math.cos(math.radians(angle)) + yy * math.sin(math.radians(angle))
    proj = (proj - proj.min()) / max(float(np.ptp(proj)), 1e-6)
    a, b = np.array(c0, float), np.array(c1, float)
    img = a + (b - a) * proj[:, :, None]
    return Image.fromarray(img.astype(np.uint8), "RGB")


def bar(d, cx, cy, w, h, color):
    """Senkrechter Balken mit runden Enden."""
    h = max(h, w)
    d.rounded_rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
                        radius=w / 2, fill=color + (255,))


def render(body=BODY_APP, shadow=True, small=False):
    """Zeichnet das Symbol in CANVAS x CANVAS.

    body    Kantenlaenge des Korpus im 1024er Raster.
    shadow  weicher Schlagschatten -- nur fuers App-Bundle, nicht fuers Web.
    small   vereinfachte Fassung fuer 16/32 px: vier statt sechs Balken und
            ein kraeftigerer Kartenrand. Ohne das verschmelzen die Balken
            beim Verkleinern zu einem grauen Block.
    """
    S = CANVAS * SS
    B = body * SS
    c = S / 2
    left, right = c - B / 2, c + B / 2

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(superellipse(c, c, B / 2), fill=255)

    if shadow:
        blur = mask.filter(ImageFilter.GaussianBlur(20 * SS))
        blur = blur.point(lambda v: int(v * 85 / 255))
        shade = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        shade.paste(Image.new("RGBA", (S, S), (0, 0, 0, 255)), (0, 14 * SS), blur)
        img.alpha_composite(shade)

    img.paste(gradient(S, BG_TOP, BG_BOTTOM).convert("RGBA"), (0, 0), mask)
    d = ImageDraw.Draw(img)

    pad = B * 0.135
    x0, x1 = left + pad, right - pad
    card_top = c - B * 0.155
    card_bot = c + B * 0.335

    # Drei Reiter. Der mittlere steht hoeher: so liest man eine Reiterleiste
    # mit ausgewaehltem Reiter, nicht drei nebeneinandergelegte Farbflaechen.
    gap = (x1 - x0) * 0.028
    tab_w = ((x1 - x0) - 2 * gap) / 3
    for i, (col, lift) in enumerate(((GREEN, 0.085), (BLUE, 0.165), (RED, 0.085))):
        tx = x0 + i * (tab_w + gap)
        d.rounded_rectangle([tx, card_top - B * lift, tx + tab_w, card_top + B * 0.06],
                            radius=B * 0.034, fill=col + (255,))

    d.rounded_rectangle([x0, card_top, x1, card_bot], radius=B * 0.055,
                        fill=CARD + (255,))

    amps = AMPS_SMALL if small else AMPS
    inset = B * (0.075 if small else 0.062)
    bx0, bx1 = x0 + inset, x1 - inset
    slot = (bx1 - bx0) / len(amps)
    width = slot * (0.42 if small else 0.50)
    cy = (card_top + card_bot) / 2 + B * 0.012
    height = (card_bot - card_top) * (0.62 if small else 0.58)
    for i, a in enumerate(amps):
        bar(d, bx0 + slot * (i + 0.5), cy, width, height * a, INKBAR)

    # Sehr dezenter Lichtbogen oben -- macOS-Symbole tragen ihn fast alle.
    g = Image.new("L", (S, S), 0)
    ImageDraw.Draw(g).ellipse([left - B * 0.4, left - B, right + B * 0.4,
                               left + B * 0.5], fill=22)
    g = ImageChops.multiply(g.filter(ImageFilter.GaussianBlur(38 * SS)), mask)
    img.paste(Image.new("RGBA", (S, S), (255, 255, 255, 255)), (0, 0), g)

    return img.resize((CANVAS, CANVAS), Image.LANCZOS)


def scaled(src, size):
    return src.resize((size, size), Image.LANCZOS)


def patch_index_html(uri_128, uri_32):
    """Tauscht die beiden eingebetteten Symbole in der Oberflaeche aus.

    Beide Fundstellen werden ueber ihren Kontext angesteuert (link rel="icon"
    und das Bild mit class="app-icon"), nicht ueber ein blankes Suchmuster auf
    data:-URIs -- sonst traefe ein spaeter ergaenztes Bild versehentlich mit.
    """
    path = ROOT / "app" / "webui" / "index.html"
    html = path.read_text(encoding="utf-8")
    patterns = (
        (r'(<link rel="icon" type="image/png" sizes="32x32" href=")[^"]*(")',
         "Favicon 32", uri_32),
        (r'(<link rel="icon" type="image/png" sizes="128x128" href=")[^"]*(")',
         "Favicon 128", uri_128),
        (r'(<img class="app-icon" alt="" src=")[^"]*(")', "Kopfzeile", uri_128),
    )
    for pattern, label, uri in patterns:
        html, hits = re.subn(pattern, lambda m: m.group(1) + uri + m.group(2), html)
        if hits != 1:
            raise SystemExit(f"FEHLER: {label} in index.html {hits}x gefunden, erwartet 1x")
    path.write_text(html, encoding="utf-8")


def patch_pwa_module(b64_192, b64_512, b64_apple_touch):
    """Tauscht die drei Icon-Konstanten in app/pwa.py aus.

    Reines Base64 ohne "data:image/png;base64,"-Praefix -- die drei Icons
    werden dort als echte Bilddateien ausgeliefert (server.py), nicht als
    data:-URI. Gleiches Prinzip wie patch_index_html(): ueber den
    Variablennamen ansteuern, Treffer-Anzahl pruefen statt stillschweigend
    nichts zu ersetzen.
    """
    path = ROOT / "app" / "pwa.py"
    src = path.read_text(encoding="utf-8")
    patterns = (
        (r'(_ICON_192_B64 = ")[^"]*(")', "PWA-Icon 192", b64_192),
        (r'(_ICON_512_B64 = ")[^"]*(")', "PWA-Icon 512", b64_512),
        (r'(_ICON_APPLE_TOUCH_B64 = ")[^"]*(")', "Apple-Touch-Icon 180", b64_apple_touch),
    )
    for pattern, label, uri in patterns:
        src, hits = re.subn(pattern, lambda m: m.group(1) + uri + m.group(2), src)
        if hits != 1:
            raise SystemExit(f"FEHLER: {label} in pwa.py {hits}x gefunden, erwartet 1x")
    path.write_text(src, encoding="utf-8")


def main():
    ASSETS.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)

    app_icon = render()                       # macOS-Korpus mit Rand und Schatten
    app_small = render(small=True)            # dieselbe Form, grob gezeichnet
    flat = render(body=BODY_FLAT, shadow=False)
    flat_small = render(body=BODY_FLAT, shadow=False, small=True)

    app_icon.save(ASSETS / "icon-1024.png")

    # Iconset fuer iconutil. Bis 32 px die vereinfachte Zeichnung nehmen.
    iconset = ASSETS / "appicon.iconset"
    iconset.mkdir(exist_ok=True)
    for px, name in ((16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
                     (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
                     (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
                     (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
                     (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png")):
        scaled(app_small if px <= 32 else app_icon, px).save(iconset / name)

    icns = ASSETS / "appicon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                   check=True)

    # README: das App-Symbol so, wie es im Dock steht.
    scaled(app_icon, 256).save(DOCS / "app-icon.png")

    # Web-UI: ohne Rand, damit bei 16-34 px keine Flaeche verschenkt wird.
    fav = ASSETS / "favicon-128.png"
    scaled(flat, 128).save(fav)
    fav32 = ASSETS / "favicon-32.png"
    scaled(flat_small, 32).save(fav32)

    def as_uri(p):
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")

    # Der Browser nimmt fuers Tab-Symbol die 32er (grob gezeichnet, dort
    # lesbar), fuer Lesezeichen und Retina die 128er.
    patch_index_html(as_uri(fav), as_uri(fav32))

    # PWA-Icons: aus der gepolsterten App-Zeichnung (wie appicon.icns), nicht
    # der randlosen flat-Fassung -- eine installierte PWA bekommt auf macOS
    # ein eigenes Dock-Icon, soll also wie das App-Symbol aussehen. 180 px ist
    # Apples Standardgroesse fuer apple-touch-icon.
    icon_192 = scaled(app_icon, 192)
    icon_512 = scaled(app_icon, 512)
    icon_apple_touch = scaled(app_icon, 180)

    def png_b64(img):
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    patch_pwa_module(png_b64(icon_192), png_b64(icon_512), png_b64(icon_apple_touch))

    print(f"appicon.icns        {icns.stat().st_size // 1024} KB")
    print(f"docs/app-icon.png   256 px")
    print(f"favicon 32/128 px   in index.html eingesetzt")
    print(f"PWA-Icons 180/192/512 in app/pwa.py eingesetzt")
    print("\nJetzt `./run.command report` laufen lassen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
