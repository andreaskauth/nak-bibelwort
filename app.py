#!/usr/bin/env python3
"""NAK Bibelwort-Drucker – Flask-App für Raspberry Pi Zero 2W"""

import os, csv, json, re, threading, logging, struct
from datetime import date, datetime, timedelta
from io import StringIO

import requests
from flask import Flask, render_template_string, request, jsonify
from PIL import Image, ImageDraw, ImageFont

DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
CSV_PATH       = os.path.join(DATA_DIR, "planung.csv")
MANUELL_PATH   = os.path.join(DATA_DIR, "manuell.json")
ONLINE_PATH    = os.path.join(DATA_DIR, "online.json")
CONFIG_PATH    = os.path.join(DATA_DIR, "config.json")
FONT_PATH      = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DRUCKER_BREITE = 384
GPIO_TASTE     = 27
GPIO_LED       = 22
GOTTESDIENST_TAGE = (2, 6)

os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # Uploads (PDF/CSV) max. 25 MB

def lade_csv():
    eintraege = {}
    if not os.path.exists(CSV_PATH): return eintraege
    with open(CSV_PATH, encoding="utf-8") as f:
        for z in csv.DictReader(f, delimiter=";"):
            d = z.get("datum","").strip()
            if d: eintraege[d] = {"stelle": z.get("stelle","").strip(), "text": z.get("text","").strip()}
    return eintraege

def speichere_csv(eintraege):
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";"); w.writerow(["datum","stelle","text"])
        for d, e in sorted(eintraege.items()): w.writerow([d, e.get("stelle",""), e.get("text","")])

def lade_manuell():
    if not os.path.exists(MANUELL_PATH): return {}
    with open(MANUELL_PATH, encoding="utf-8") as f: return json.load(f)

def speichere_manuell(e):
    with open(MANUELL_PATH, "w", encoding="utf-8") as f: json.dump(e, f, ensure_ascii=False, indent=2)

def lade_online():
    if not os.path.exists(ONLINE_PATH): return {}
    with open(ONLINE_PATH, encoding="utf-8") as f: return json.load(f)

def speichere_online(e):
    with open(ONLINE_PATH, "w", encoding="utf-8") as f: json.dump(e, f, ensure_ascii=False, indent=2)

def lade_config():
    if not os.path.exists(CONFIG_PATH): return {}
    with open(CONFIG_PATH, encoding="utf-8") as f: return json.load(f)

def speichere_config(c):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f: json.dump(c, f, ensure_ascii=False, indent=2)

FEED_STATUS_PATH = os.path.join(DATA_DIR, "feed_status.json")
FEED_WARNTAGE    = 14   # so viele Tage vor Feed-Ende wird gewarnt

def lade_feed_status():
    if not os.path.exists(FEED_STATUS_PATH): return {}
    try:
        with open(FEED_STATUS_PATH, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def speichere_feed_status(s):
    with open(FEED_STATUS_PATH, "w", encoding="utf-8") as f: json.dump(s, f, ensure_ascii=False, indent=2)

def _json_hinweis(msg):
    """Übersetzt typische JSON-Fehlermeldungen in verständliche Hinweise."""
    if "Expecting ',' delimiter" in msg: return "Komma fehlt – meist am Ende der Zeile davor"
    if "Expecting property name" in msg: return "überzähliges Komma vor einer schließenden Klammer oder fehlendes Anführungszeichen"
    if "Expecting value" in msg:         return "Wert fehlt oder überzähliges Komma"
    if "Invalid control character" in msg: return "Zeilenumbruch innerhalb eines Textes"
    if "Unterminated string" in msg:     return "Anführungszeichen nicht geschlossen"
    return msg

def aktualisiere_online_feed():
    """
    Ruft den Online-Feed ab und prüft ihn. Nur ein gültiger Feed überschreibt die
    lokale Kopie – bei Fehlern bleibt die zuletzt gute Version erhalten.
    Gibt True bei Erfolg zurück, False bei Fehler. Ergebnis landet in feed_status.json.
    """
    status = lade_feed_status()
    status["letzter_versuch"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    def fehler(text):
        status["fehler"] = text; speichere_feed_status(status)
        log.warning(f"Online-Feed: {text}"); return False

    url = lade_config().get("online_url","").strip()
    if not url: return fehler("Keine Feed-URL eingetragen.")
    try:
        r = requests.get(url, timeout=15); r.raise_for_status()
    except Exception as e:
        return fehler(f"Feed nicht erreichbar ({type(e).__name__}).")
    try:
        daten = json.loads(r.text)
    except json.JSONDecodeError as e:
        return fehler(f"Feed fehlerhaft in Zeile {e.lineno}, Spalte {e.colno}: {_json_hinweis(e.msg)}.")
    if not isinstance(daten, dict) or not isinstance(daten.get("bibelworte"), list):
        return fehler('Feed hat nicht das erwartete Format {"bibelworte": [...]}.')

    eintraege, ungueltig = {}, []
    for e in daten["bibelworte"]:
        if not isinstance(e, dict): ungueltig.append("?"); continue
        d = str(e.get("datum","")).strip(); t = str(e.get("text","")).strip()
        try: datetime.strptime(d, "%d.%m.%Y")
        except ValueError: ungueltig.append(d or "ohne Datum"); continue
        if not t: ungueltig.append(d); continue
        eintraege[d] = {"stelle": str(e.get("stelle","")).strip(), "text": t}
    if not eintraege:
        return fehler("Feed enthält keine gültigen Einträge.")

    speichere_online(eintraege)
    status["fehler"] = None
    status["letzter_erfolg"] = status["letzter_versuch"]
    status["warnung"] = (f"{len(ungueltig)} Eintrag/Einträge übersprungen (Datum oder Text ungültig): "
                         + ", ".join(ungueltig[:5])) if ungueltig else None
    speichere_feed_status(status)
    log.info(f"Online-Feed: {len(eintraege)} Einträge, {len(ungueltig)} übersprungen.")
    return True

def hinweise():
    """Hinweise oben auf der Seite: [(stufe, text), …] – Stufe 'warn' oder 'err'."""
    hinweis = []
    url_gesetzt = bool(lade_config().get("online_url","").strip())
    quellen = [lade_leitgedanken(), lade_csv()] + ([lade_online()] if url_gesetzt else [])
    daten = []
    for q in quellen:
        for d in q:
            try: daten.append(datetime.strptime(d, "%d.%m.%Y").date())
            except ValueError: pass
    if not daten:
        hinweis.append(("err", "Es sind keine Bibelworte hinterlegt. Bitte ein Leitgedanken-PDF hochladen."))
    else:
        bis = max(daten); rest = (bis - date.today()).days
        if rest < 0:
            hinweis.append(("err", f"Die hinterlegten Bibelworte reichen nur bis {bis:%d.%m.%Y}. "
                                   "Bitte das nächste Leitgedanken-PDF hochladen."))
        elif rest < FEED_WARNTAGE:
            hinweis.append(("warn", f"Die hinterlegten Bibelworte reichen nur noch bis {bis:%d.%m.%Y}. "
                                    "Bitte das nächste Leitgedanken-PDF hochladen."))
    fehler = lade_feed_status().get("fehler")
    if url_gesetzt and fehler:
        hinweis.append(("err", f"Online-Feed: {fehler}"))
    return hinweis

def feed_info():
    """Zusammenfassung für die Weboberfläche: Stufe ok/warn/err, Statuszeile, ggf. Banner."""
    url = lade_config().get("online_url","").strip()
    if not url:
        return {"level": "ok", "text": "Kein Online-Feed eingerichtet.", "detail": None, "banner": None}
    st = lade_feed_status(); online = lade_online()
    daten = []
    for d in online:
        try: daten.append(datetime.strptime(d, "%d.%m.%Y").date())
        except ValueError: pass
    bis = max(daten) if daten else None
    teile = [f"{len(online)} Einträge"]
    if bis: teile.append(f"reicht bis {bis.strftime('%d.%m.%Y')}")
    if st.get("letzter_erfolg"): teile.append(f"zuletzt abgerufen {st['letzter_erfolg']}")
    text = " · ".join(teile)

    level, detail, banner = "ok", None, None
    if not bis:
        level, banner = "err", "Es ist noch kein Online-Feed geladen."
    else:
        rest = (bis - date.today()).days
        if rest < 0:
            level, banner = "err", f"Der Online-Feed ist abgelaufen (letzter Eintrag {bis.strftime('%d.%m.%Y')}). Bitte neue Bibelworte einpflegen."
        elif rest < FEED_WARNTAGE:
            level, banner = "warn", f"Der Online-Feed reicht nur noch bis {bis.strftime('%d.%m.%Y')}. Bitte neue Bibelworte einpflegen."
    if st.get("warnung"):
        detail = st["warnung"]
        if level == "ok": level = "warn"
    if st.get("fehler"):
        detail = st["fehler"] + (" Es werden die zuletzt geladenen Daten verwendet." if online else "")
        level = "err"
        banner = banner or f"Online-Feed: {st['fehler']}"
    return {"level": level, "text": text, "detail": detail, "banner": banner}

def lade_eintrag(datum_str):
    """
    Lädt das gültige Bibelwort für ein Datum.
    - Manuell gilt NUR für das exakte Datum
    - Leitgedanken-PDF, CSV und Online: neuester Eintrag <= datum gilt (läuft weiter)
    - Bei gleichem Datum: manuell > Leitgedanken (PDF) > CSV > online
    """
    ziel = datetime.strptime(datum_str, "%d.%m.%Y").date()

    # 1. Manueller Eintrag für genau dieses Datum?
    manuell = lade_manuell()
    if datum_str in manuell:
        r = manuell[datum_str].copy()
        r["quelle"] = "manuell"
        r["eintrag_datum"] = datum_str
        return r

    # 2. Neuester Eintrag <= zieldatum aus PDF/CSV/Online (kleinere Zahl = höherer Rang)
    kandidaten = {}
    for quelle, rang, daten in (("online", 3, lade_online()), ("csv", 2, lade_csv()),
                                ("pdf", 1, lade_leitgedanken())):
        for d, e in daten.items():
            try: dt = datetime.strptime(d, "%d.%m.%Y").date()
            except ValueError: continue
            if dt <= ziel and (dt not in kandidaten or kandidaten[dt][2] > rang):
                kandidaten[dt] = ({"stelle": e.get("stelle",""), "text": e.get("text","")}, quelle, rang)

    if not kandidaten: return None
    nd = max(kandidaten.keys()); e, q, _ = kandidaten[nd]
    r = e.copy(); r["quelle"] = q; r["eintrag_datum"] = nd.strftime("%d.%m.%Y"); return r

def setze_manuell(datum_str, stelle, text):
    m = lade_manuell(); m[datum_str] = {"stelle": stelle, "text": text}; speichere_manuell(m)

def loesche_manuell(datum_str):
    m = lade_manuell()
    if datum_str in m: del m[datum_str]; speichere_manuell(m)

def naechste_gottesdienste(wochen=4):
    heute = date.today(); ende = heute + timedelta(weeks=wochen)
    datum_set = set()
    tag = heute
    while tag <= ende:
        if tag.weekday() in GOTTESDIENST_TAGE: datum_set.add(tag.strftime("%d.%m.%Y"))
        tag += timedelta(days=1)
    for q in [lade_manuell(), lade_leitgedanken(), lade_csv(), lade_online()]:
        for d in q.keys():
            try:
                dt = datetime.strptime(d, "%d.%m.%Y").date()
                if heute <= dt <= ende: datum_set.add(d)
            except: pass
    tage = []
    for ds in sorted(datum_set, key=lambda d: datetime.strptime(d, "%d.%m.%Y")):
        dt = datetime.strptime(ds, "%d.%m.%Y").date()
        e = lade_eintrag(ds) or {}
        tage.append({"datum": ds, "wochentag": ["Mo","Di","Mi","Do","Fr","Sa","So"][dt.weekday()],
                     "heute": dt == heute, "stelle": e.get("stelle",""),
                     "text": e.get("text",""), "quelle": e.get("quelle","—")})
    return tage

# ══════════════════════════════════════════════════════════════════════════════
# LEITGEDANKEN-PDF AUSWERTEN
# ══════════════════════════════════════════════════════════════════════════════
# Liest aus dem monatlichen Heft „Leitgedanken zum Gottesdienst" je Gottesdienst
# Datum, Bibelstelle und Bibeltext (aus dem farbigen Kasten oben auf der Seite)
# und gleicht das Ergebnis mit der Kalenderübersicht hinten im Heft ab.
# Benötigt: sudo apt install poppler-utils   (stellt „pdftotext" bereit)

MONATE = ["Januar","Februar","März","April","Mai","Juni","Juli",
          "August","September","Oktober","November","Dezember"]
WOCHENTAGE_KURZ = ["Mo","Di","Mi","Do","Fr","Sa","So"]

# Bibelstelle wie „Ps 130,7b", „1Kor 12,13", „Phil 3,20.21", „Lk 1,46–48", „Röm 13,11"
_STELLE = r"(?:[1-3]\s?)?[A-ZÄÖÜ][a-zäöü]{1,5}\s?\d{1,3},\d{1,3}[a-f]?(?:\s?[.\-–]\s?\d{1,3}[a-f]?)*"
_RE_STELLE_ALLEIN = re.compile(rf"^\s*({_STELLE})\s*$")
_RE_STELLE_AM_ENDE = re.compile(rf"^(.*\S)\s+({_STELLE})\s*$")
_RE_KOPF_DATUM = re.compile(r"^(.*?)\s{2,}(\d{1,2})\.(?:/(\d{1,2})\.)?(\d{1,2})\.(\d{4})\s*$")
_RE_KAL_TAG = re.compile(r"(?<!\S)(\d{1,2}) (Mo|Di|Mi|Do|Fr|Sa|So)\b")

def _bereinige_zeile(z):
    """Steuerzeichen und Sonderleerzeichen aus pdftotext in normale Leerzeichen."""
    z = z.replace("\t", " ").replace(" ", " ").replace(" ", " ").replace(" ", " ")
    z = re.sub("[​-‏⁠﻿]", "", z)   # unsichtbare Zeichen entfernen
    return "".join(c if (c >= " " or c == "\n") else " " for c in z).rstrip()

def _stelle_norm(s):
    return re.sub(r"\s+", "", s).replace("–", "-").lower()

def _fuege_text_zusammen(zeilen):
    """Zeilen des Kastens zu einem Text verbinden, Silbentrennung am Zeilenende auflösen."""
    text = ""
    for z in (z.strip() for z in zeilen):
        if not z: continue
        if text.endswith("­"):                       # weicher Trennstrich
            text = text[:-1] + z
        elif text.endswith("-") and z[:1].islower():      # „gekom-" + „men"
            text = text[:-1] + z
        elif text.endswith("-"):                          # echter Bindestrich vor Großschreibung
            text += z
        else:
            text = (text + " " + z) if text else z
    return text.replace("­", "")

def _waehle_datum(tag1, tag2, monat, jahr):
    """Bei „4./5.11." den Tag wählen, an dem die Gemeinde Gottesdienst hat (GOTTESDIENST_TAGE)."""
    kandidaten = [date(jahr, monat, tag1)]
    if tag2: kandidaten.append(date(jahr, monat, tag2))
    for d in kandidaten:
        if d.weekday() in GOTTESDIENST_TAGE: return d
    return kandidaten[0]

def _lies_gottesdienstseite(seite):
    """Gibt (titel, datum, stelle, text) zurück oder None, wenn es keine Gottesdienstseite ist."""
    zeilen = [_bereinige_zeile(z) for z in seite.split("\n")]
    zeilen = [z for z in zeilen if z.strip()]
    if not zeilen: return None
    m = _RE_KOPF_DATUM.match(zeilen[0].strip())
    if not m: return None
    titel = m.group(1).strip()
    datum = _waehle_datum(int(m.group(2)), int(m.group(3)) if m.group(3) else None,
                          int(m.group(4)), int(m.group(5)))
    text_zeilen, stelle = [], None
    for z in zeilen[1:25]:
        allein = _RE_STELLE_ALLEIN.match(z)
        if allein:
            stelle = allein.group(1); break
        if z.startswith("  "):          # eingerückt = Fließtext der Predigthilfe → Kasten zu Ende
            break
        am_ende = _RE_STELLE_AM_ENDE.match(z)
        if am_ende:
            text_zeilen.append(am_ende.group(1)); stelle = am_ende.group(2); break
        text_zeilen.append(z)
    if not stelle or not text_zeilen: return None
    stelle = re.sub(r"\s+", " ", stelle).strip()
    return titel, datum, stelle, _fuege_text_zusammen(text_zeilen)

def _lies_kalenderseite(seite, ausgabe_monat, ausgabe_jahr):
    """Kalenderübersicht hinten im Heft → {date: stelle}. Leeres Dict, wenn keine Kalenderseite."""
    zeilen = [_bereinige_zeile(z) for z in seite.split("\n")]
    kopf = next((z for z in zeilen if z.strip()), "")
    monate = [MONATE.index(w) + 1 for w in kopf.split() if w in MONATE]
    if len(monate) != 2: return {}
    def jahr_von(mon):
        return ausgabe_jahr + 1 if mon < ausgabe_monat else ausgabe_jahr
    ergebnis, tage = {}, 0
    for z in zeilen:
        treffer = list(_RE_KAL_TAG.finditer(z))
        for i, t in enumerate(treffer):
            spalte = 0 if t.start() < 30 else 1
            ende = treffer[i+1].start() if i + 1 < len(treffer) else len(z)
            rest = z[t.end():ende]
            tag, mon = int(t.group(1)), monate[spalte]
            try: d = date(jahr_von(mon), mon, tag)
            except ValueError: continue
            tage += 1
            if WOCHENTAGE_KURZ[d.weekday()] != t.group(2): continue   # Plausibilität: Wochentag passt?
            s = re.search(_STELLE, rest)
            if s: ergebnis[d] = re.sub(r"\s+", " ", s.group(0)).strip()
    return ergebnis if tage >= 20 else {}

def werte_leitgedanken_aus(pdf_pfad):
    """
    Wertet ein Leitgedanken-PDF aus. Rückgabe:
      {"ausgabe": "November 2026", "schluessel": "2026-11",
       "eintraege": [{datum, wochentag, titel, stelle, text, kalender, hinweis}],
       "warnungen": [...]}
    Wirft RuntimeError mit verständlicher Meldung, wenn etwas nicht klappt.
    """
    import subprocess
    try:
        r = subprocess.run(["pdftotext", "-layout", pdf_pfad, "-"],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError("pdftotext fehlt auf dem Gerät – bitte einmalig "
                           "„sudo apt install poppler-utils“ ausführen.")
    if r.returncode != 0:
        raise RuntimeError("Die Datei konnte nicht als PDF gelesen werden.")
    seiten = r.stdout.split("\f")

    # Ausgabe (Monat/Jahr) bestimmen: Titelseite oder Vorwort
    m = re.search(r"Monatsschrift für Geistliche\s+(\w+)\s+(\d{4})", r.stdout) \
        or re.search(r"Vorwort\s+(\w+)\s+(\d{4})", r.stdout)
    if not m or m.group(1) not in MONATE:
        raise RuntimeError("Das scheint kein Heft „Leitgedanken zum Gottesdienst“ zu sein "
                           "(Monat/Jahr nicht gefunden).")
    a_monat, a_jahr = MONATE.index(m.group(1)) + 1, int(m.group(2))

    gottesdienste, kalender, warnungen = {}, {}, []
    for seite in seiten:
        gd = _lies_gottesdienstseite(seite)
        if gd:
            titel, d, stelle, text = gd
            if d in gottesdienste:
                warnungen.append(f"{d:%d.%m.} kommt doppelt vor – erste Seite wird verwendet.")
                continue
            gottesdienste[d] = (titel, stelle, text)
            continue
        if not kalender:
            kalender = _lies_kalenderseite(seite, a_monat, a_jahr)

    if not gottesdienste:
        raise RuntimeError("Im PDF wurden keine Gottesdienstseiten erkannt.")
    if not kalender:
        warnungen.append("Kalenderübersicht nicht gefunden – Abgleich nicht möglich.")

    eintraege = []
    for d in sorted(gottesdienste):
        titel, stelle, text = gottesdienste[d]
        kal = kalender.get(d)
        hinweis = None
        if kalender and kal is None:
            hinweis = "fehlt in der Kalenderübersicht"
        elif kal and _stelle_norm(kal) != _stelle_norm(stelle):
            hinweis = f"Kalenderübersicht nennt {kal}"
            warnungen.append(f"{d:%d.%m.}: Seite nennt {stelle}, Kalenderübersicht {kal}. "
                             "Übernommen wird die Seite (dort steht der vollständige Text).")
        eintraege.append({"datum": d.strftime("%d.%m.%Y"),
                          "wochentag": WOCHENTAGE_KURZ[d.weekday()],
                          "titel": titel, "stelle": stelle, "text": text,
                          "kalender": kal, "hinweis": hinweis})

    # Kalendereinträge der Ausgabe ohne eigene Seite melden
    for d, s in sorted(kalender.items()):
        if d.month == a_monat and d.year == a_jahr and d not in gottesdienste:
            warnungen.append(f"{d:%d.%m.}: Kalenderübersicht nennt {s}, aber im Heft gibt es "
                             "dazu keine Seite – nicht übernommen.")

    return {"ausgabe": f"{MONATE[a_monat-1]} {a_jahr}",
            "schluessel": f"{a_jahr}-{a_monat:02d}",
            "eintraege": eintraege, "warnungen": warnungen}

# ── Ablage der ausgewerteten Hefte ───────────────────────────────────────────
LEITGEDANKEN_PATH = os.path.join(DATA_DIR, "leitgedanken.json")
PDF_ORDNER        = os.path.join(DATA_DIR, "pdfs")
PDF_BEHALTEN      = 6     # so viele Hefte (PDFs) bleiben gespeichert, ältere werden gelöscht
os.makedirs(PDF_ORDNER, exist_ok=True)

def lade_leitgedanken():
    if not os.path.exists(LEITGEDANKEN_PATH): return {}
    with open(LEITGEDANKEN_PATH, encoding="utf-8") as f: return json.load(f)

def speichere_leitgedanken(e):
    with open(LEITGEDANKEN_PATH, "w", encoding="utf-8") as f: json.dump(e, f, ensure_ascii=False, indent=2)

def gespeicherte_hefte():
    """Liste der gespeicherten Hefte, neueste zuerst: [("2026-12", "Dezember 2026"), …]"""
    hefte = []
    for name in os.listdir(PDF_ORDNER):
        m = re.match(r"^(\d{4})-(\d{2})\.pdf$", name)
        if m: hefte.append((f"{m.group(1)}-{m.group(2)}", f"{MONATE[int(m.group(2))-1]} {m.group(1)}"))
    return sorted(hefte, reverse=True)

def raeume_pdfs_auf():
    """Nur die neuesten PDF_BEHALTEN Hefte behalten. Die ausgelesenen Bibelworte bleiben erhalten."""
    geloescht = []
    for schluessel, name in gespeicherte_hefte()[PDF_BEHALTEN:]:
        try:
            os.remove(os.path.join(PDF_ORDNER, f"{schluessel}.pdf")); geloescht.append(name)
        except OSError: pass
    if geloescht: log.info(f"Alte PDFs gelöscht: {', '.join(geloescht)}")
    return geloescht

LED_OBJ = None  # wird in init_gpio gesetzt, damit Scheduler/API die LED aktualisieren können

def zeige_aktuelles(force=False):
    """Display und LED mit dem aktuell gültigen Bibelwort aktualisieren.
    force=True zeichnet auch dann neu, wenn sich der Inhalt nicht geändert hat."""
    heute = date.today().strftime("%d.%m.%Y")
    e = lade_eintrag(heute)
    hat_wort = bool(e and e.get("text"))
    if hat_wort:
        aktualisiere_display(e.get("stelle",""), e["text"], heute, force=force)
    if LED_OBJ is not None and not ap_aktiv:
        LED_OBJ.on() if hat_wort else LED_OBJ.off()
    return hat_wort

def starte_scheduler():
    """Täglich um 3 Uhr: Feed abrufen UND Display/LED auf den neuen Tag umstellen."""
    import time
    def loop():
        while True:
            jetzt = datetime.now()
            naechster = jetzt.replace(hour=3, minute=0, second=0, microsecond=0)
            if naechster <= jetzt: naechster += timedelta(days=1)
            time.sleep((naechster - jetzt).total_seconds())
            aktualisiere_online_feed()
            zeige_aktuelles(force=True)   # tägliche Vollauffrischung beugt Geisterbildern vor
            log.info("Täglicher Lauf: Feed + Display aktualisiert.")
    threading.Thread(target=loop, daemon=True).start()

def erstelle_druckbild(stelle, text, datum_str):
    breite = DRUCKER_BREITE
    fgs = 28 if len(text) < 80 else 26 if len(text) < 150 else 24
    try:
        fs = ImageFont.truetype(FONT_BOLD_PATH, fgs+4)
        ft = ImageFont.truetype(FONT_PATH, fgs)
        fd = ImageFont.truetype(FONT_PATH, fgs-4)
    except: fs = ft = fd = ImageFont.load_default()
    def umbreche(t, g):
        woerter = t.split(); zeilen, zeile = [], ""
        for w in woerter:
            probe = (zeile+" "+w).strip()
            if len(probe)*g*0.54 < breite-30: zeile = probe
            else:
                if zeile: zeilen.append(zeile)
                zeile = w
        if zeile: zeilen.append(zeile)
        return zeilen
    zeilen = umbreche(text, fgs); zh = fgs+8
    hoehe = max(400, 20+4+8+(fgs-4)+10+4+8+(fgs+4)+10+4+8+len(zeilen)*zh+10+4+20)
    img = Image.new("1", (breite, hoehe), 1); draw = ImageDraw.Draw(img)
    y = 20
    draw.line([(10,y),(breite-10,y)], fill=0, width=3); y += 8
    draw.text((breite//2,y), datum_str, font=fd, fill=0, anchor="mt"); y += (fgs-4)+10
    draw.line([(20,y),(breite-20,y)], fill=0, width=2); y += 8
    draw.text((breite//2,y), stelle, font=fs, fill=0, anchor="mt"); y += (fgs+4)+10
    draw.line([(20,y),(breite-20,y)], fill=0, width=2); y += 8
    for z in zeilen:
        draw.text((breite//2,y), z, font=ft, fill=0, anchor="mt"); y += zh
    y += 10; draw.line([(10,y),(breite-10,y)], fill=0, width=3)
    return img

def drucke_bibelwort(stelle, text, datum_str):
    try:
        from escpos.printer import Usb
        img = erstelle_druckbild(stelle, text, datum_str)
        drucker = Usb(0x28e9, 0x0289, timeout=0, out_ep=0x3, in_ep=0x81)
        def sende(teil):
            w, h = teil.size; wb = (w+7)//8; px = teil.load()
            data = bytearray()
            for row in range(h):
                for x in range(0, w, 8):
                    byte = 0
                    for bit in range(8):
                        if x+bit < w and px[x+bit,row] == 0: byte |= (1<<(7-bit))
                    data.append(byte)
            drucker._raw(b'\x1d\x76\x30\x00' + struct.pack('<H',wb) + struct.pack('<H',h) + bytes(data))
        bw, bh = img.size
        for y in range(0, bh, 200): sende(img.crop((0,y,bw,min(y+200,bh))))
        drucker.text("\n\n\n"); drucker.cut(); drucker.close()
        return True
    except Exception as e: log.error(f"Druckfehler: {e}"); return False

# ── Display-Steuerung ─────────────────────────────────────────────────────────
# Früher startete jeder Aufruf einen eigenen Thread. Kamen zwei Aufrufe kurz
# hintereinander (z. B. beim Boot: lokale Daten + nach Feed-Abruf), schrieben
# zwei Threads gleichzeitig über SPI aufs Display → halb gezeichnetes Bild.
# Jetzt gibt es genau einen Zeichen-Worker; neue Wünsche während des Zeichnens
# werden gesammelt, und nur der jeweils neueste wird danach gezeichnet.
_display_lock = threading.Lock()
_display_wunsch = None
_display_zuletzt = None
_display_worker_laeuft = False

def aktualisiere_display(stelle, text, datum_str, force=False):
    global _display_wunsch, _display_worker_laeuft
    inhalt = (stelle, text, datum_str)
    with _display_lock:
        if not force and _display_wunsch is None and inhalt == _display_zuletzt:
            return  # steht schon so auf dem Display – kein unnötiges Neuzeichnen
        _display_wunsch = inhalt
        if _display_worker_laeuft:
            return  # laufender Worker übernimmt den neuesten Wunsch
        _display_worker_laeuft = True
    threading.Thread(target=_display_worker, daemon=True).start()

def _display_worker():
    global _display_wunsch, _display_worker_laeuft, _display_zuletzt
    while True:
        with _display_lock:
            inhalt, _display_wunsch = _display_wunsch, None
            if inhalt is None:
                _display_worker_laeuft = False
                return
        for versuch in (1, 2):
            if _zeichne_display(*inhalt):
                with _display_lock: _display_zuletzt = inhalt
                break
            if versuch == 1:
                import time; time.sleep(3)
                log.info("Display: zweiter Versuch…")

def _zeichne_display(stelle, text, datum_str):
    """Zeichnet einmal vollständig: init → weiß löschen → Bild → Schlafmodus."""
    epd = None
    try:
            from waveshare_epd import epd2in13_V4
            epd = epd2in13_V4.EPD(); epd.init()
            try: epd.Clear(0xFF)   # vollständig weiß – verhindert Reste/Geisterbild
            except Exception: pass
            img = Image.new("1", (epd.height, epd.width), 255)
            draw = ImageDraw.Draw(img)
            try:
                fd = ImageFont.truetype(FONT_PATH, 11)
                fs = ImageFont.truetype(FONT_BOLD_PATH, 13)
                ft = ImageFont.truetype(FONT_PATH, 11)
            except: fd = fs = ft = ImageFont.load_default()
            W, H = epd.height, epd.width
            draw.text((W//2,2), datum_str, font=fd, fill=0, anchor="mt")
            draw.line([(0,16),(W,16)], fill=0, width=1)
            draw.text((W//2,20), stelle, font=fs, fill=0, anchor="mt")
            draw.line([(0,36),(W,36)], fill=0, width=1)
            woerter = text.split(); zeilen, zeile = [], ""
            for w in woerter:
                probe = (zeile+" "+w).strip()
                if len(probe)*6.2 < W-8: zeile = probe
                else:
                    if zeile: zeilen.append(zeile)
                    zeile = w
            if zeile: zeilen.append(zeile)
            zh = 14; tbh = len(zeilen[:5])*zh
            y = 38 + max(0,(H-38-tbh)//2)
            for z in zeilen[:5]:
                draw.text((W//2,y), z, font=ft, fill=0, anchor="mt"); y += zh
            epd.display(epd.getbuffer(img)); epd.sleep()
            log.info("Display aktualisiert.")
            return True
    except ImportError:
        log.warning("waveshare_epd nicht verfügbar"); return True  # kein Display → nicht wiederholen
    except Exception as e:
        log.error(f"Display-Fehler: {e}")
        try:
            if epd: epd.sleep()   # SPI/Pins sauber freigeben für den nächsten Versuch
        except Exception: pass
        return False

ap_aktiv = False

def ist_wlan_verbunden():
    import subprocess
    try:
        r = subprocess.run(["nmcli","-t","-f","STATE","general"], capture_output=True, text=True, timeout=5)
        return "connected" in r.stdout
    except: return False

def starte_ap():
    global ap_aktiv
    import subprocess
    subprocess.run(["sudo","ip","addr","add","192.168.4.1/24","dev","wlan0"], capture_output=True)
    subprocess.run(["sudo","systemctl","start","hostapd"], capture_output=True)
    subprocess.run(["sudo","systemctl","start","dnsmasq"], capture_output=True)
    ap_aktiv = True; log.info("AP gestartet.")

def stoppe_ap():
    global ap_aktiv
    import subprocess
    subprocess.run(["sudo","systemctl","stop","hostapd"], capture_output=True)
    subprocess.run(["sudo","ip","addr","del","192.168.4.1/24","dev","wlan0"], capture_output=True)
    ap_aktiv = False; log.info("AP gestoppt.")

def pruefe_wlan_und_ap():
    import time; time.sleep(15)
    if not ist_wlan_verbunden(): starte_ap()

def init_gpio():
    """
    Taste:  kurz (< 3 s)  = drucken
            3–15 s        = Feed abrufen + Display aktualisieren (LED geht bei 3 s aus)
            ≥ 15 s        = Access Point ein/aus (LED geht bei 15 s wieder an)
    """
    global LED_OBJ
    try:
        from gpiozero import Button, LED
        import time
        taste = Button(GPIO_TASTE, pull_up=False, bounce_time=0.1)
        led = LED(GPIO_LED)
        LED_OBJ = led
        e = lade_eintrag(date.today().strftime("%d.%m.%Y"))
        led.on() if e and e.get("text") else led.off()
        def taste_gedrueckt():
            t0 = time.time()
            stufe = 0
            # Während des Haltens LED als Rückmeldung umschalten
            while taste.is_pressed and time.time()-t0 < 20:
                d = time.time()-t0
                if stufe == 0 and d >= 3:  led.off(); stufe = 1   # „loslassen = aktualisieren“
                if stufe == 1 and d >= 15: led.on();  stufe = 2   # „loslassen = AP“
                time.sleep(0.05)
            dauer = time.time()-t0
            if 3 <= dauer < 15:
                log.info("Taste lang: Feed + Display werden aktualisiert.")
                for _ in range(3): led.on(); time.sleep(0.15); led.off(); time.sleep(0.15)
                aktualisiere_online_feed()
                zeige_aktuelles(force=True)
                return
            if dauer >= 15:
                if ap_aktiv: stoppe_ap(); led.on()
                else:
                    starte_ap()
                    def puls():
                        while ap_aktiv: led.on(); time.sleep(1); led.off(); time.sleep(1)
                        led.on()
                    threading.Thread(target=puls, daemon=True).start()
            else:
                ds = date.today().strftime("%d.%m.%Y"); e = lade_eintrag(ds)
                if not e or not e.get("text"):
                    for _ in range(8): led.off(); time.sleep(0.08); led.on(); time.sleep(0.08)
                    return
                led.off()
                ok = drucke_bibelwort(e.get("stelle",""), e["text"], ds)
                if ok: led.on()
                else:
                    for _ in range(8): led.off(); time.sleep(0.08); led.on(); time.sleep(0.08)
        taste.when_pressed = taste_gedrueckt
        log.info("GPIO initialisiert (GPIO27 Taste, GPIO22 LED).")
    except Exception as e: log.warning(f"GPIO nicht verfügbar: {e}")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NAK Bibelwort</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@latest/tabler-icons.min.css">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7f6f2;color:#1a1a1a;max-width:620px;margin:0 auto;padding:16px;font-size:14px;line-height:1.5}
.header{text-align:center;padding:24px 0 20px;border-bottom:1px solid #e8e6e0;margin-bottom:20px}
.header-eyebrow{font-size:10px;letter-spacing:.12em;color:#999;text-transform:uppercase;margin-bottom:6px}
.header-title{font-size:20px;font-weight:500;color:#1a1a1a;letter-spacing:-.01em}
.header-date{font-size:12px;color:#888;margin-top:4px}
.card{background:#fff;border:1px solid #ede9e0;border-radius:12px;padding:18px;margin-bottom:14px}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid #f0ece4}
.card-title{font-size:11px;font-weight:500;color:#888;letter-spacing:.06em;text-transform:uppercase}
.badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:99px;font-weight:500}
.badge-manuell{background:#fef9ec;color:#9a6700;border:1px solid #fde9a0}
.badge-csv{background:#edf7f0;color:#1a7a45;border:1px solid #b8e6ca}
.badge-online{background:#eef2fe;color:#3651c4;border:1px solid #c0ccf8}
.badge-leer{background:#fdf0ee;color:#b0392b;border:1px solid #f8c4be}
.badge-pdf{background:#f4f0fa;color:#5b3f8f;border:1px solid #dcd0ef}
.hinweistext{font-size:11px;color:#999;margin-bottom:10px;line-height:1.5}
.vorschau-zeile{display:grid;grid-template-columns:62px 1fr;gap:8px;padding:9px 0;border-bottom:1px solid #f5f2ec}
.vorschau-zeile:last-child{border-bottom:none}
.vorschau-text{font-size:12px;color:#555;line-height:1.45;margin-top:2px}
label{display:block;font-size:11px;color:#999;margin:12px 0 4px;letter-spacing:.03em}
input[type=text],input[type=password],textarea{width:100%;padding:8px 11px;border:1px solid #e0ddd6;border-radius:8px;font-size:13px;font-family:inherit;background:#fff;color:#1a1a1a;outline:none;transition:border .15s}
input:focus,textarea:focus{border-color:#aaa}
textarea{min-height:80px;resize:vertical}
.row{display:flex;gap:8px}
.row input{flex:1}
.btn{display:inline-flex;align-items:center;gap:5px;padding:8px 14px;border-radius:8px;border:1px solid #ddd;background:#fff;color:#555;font-size:12px;font-weight:500;cursor:pointer;transition:background .12s;white-space:nowrap}
.btn:hover{background:#f5f3ef;border-color:#ccc}
.btn-primary{background:#1a1a1a;border-color:#1a1a1a;color:#fff}
.btn-primary:hover{background:#333;border-color:#333}
.btn-danger{color:#b0392b;border-color:#f8c4be}
.btn-danger:hover{background:#fdf0ee}
.btn-icon{padding:8px 10px}
.btn-sm{padding:5px 10px;font-size:11px}
.btn-group{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}
.msg{margin-top:10px;padding:8px 12px;border-radius:8px;font-size:12px;display:none}
.msg-ok{background:#edf7f0;color:#1a7a45;border:1px solid #b8e6ca}
.msg-err{background:#fdf0ee;color:#b0392b;border:1px solid #f8c4be}
.planrow{display:grid;grid-template-columns:76px 1fr auto;gap:8px;align-items:center;padding:9px 0;border-bottom:1px solid #f5f2ec}
.planrow:last-child{border-bottom:none}
.plan-date{font-size:11px;color:#999;line-height:1.4}
.plan-heute{font-size:10px;color:#4a4ae0;font-weight:600;display:block}
.plan-stelle{font-size:13px;font-weight:500;color:#1a1a1a}
.plan-text{font-size:11px;color:#aaa;margin-top:1px}
.plan-empty{font-size:12px;color:#ccc;font-style:italic}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
@media (max-width:560px){.grid2{grid-template-columns:minmax(0,1fr);gap:0}}
.scan-list{border:1px solid #ede9e0;border-radius:8px;max-height:180px;overflow-y:auto;margin-bottom:8px;display:none}
.scan-item{padding:8px 12px;border-bottom:1px solid #f5f5f5;cursor:pointer;display:flex;justify-content:space-between;align-items:center;font-size:12px}
.scan-item:hover{background:#f9f8f5}
.scan-item:last-child{border-bottom:none}
.scan-signal{font-size:10px;color:#bbb}
.footer{text-align:center;padding:16px 0 4px;font-size:10px;color:#ccc;letter-spacing:.06em;text-transform:uppercase}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:100;justify-content:center;align-items:flex-start;padding-top:48px}
.modal-box{background:#fff;border-radius:14px;padding:22px;width:90%;max-width:480px;border:1px solid #ede9e0}
.modal-title{font-size:14px;font-weight:500;color:#1a1a1a;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #f0ece4}
.feed-status{font-size:11px;line-height:1.45;margin-top:10px;padding:8px 10px;border-radius:8px}
.feed-ok{background:#f5f3ef;color:#777}
.feed-warn{background:#fef9ec;color:#9a6700;border:1px solid #fde9a0}
.feed-err{background:#fdf0ee;color:#b0392b;border:1px solid #f8c4be}
.feed-detail{display:block;margin-top:4px}
.banner{display:flex;gap:8px;align-items:flex-start;font-size:12px;line-height:1.45;padding:10px 12px;border-radius:10px;margin-bottom:14px}
</style>
</head>
<body>

<div class="header">
  <div class="header-eyebrow">Neuapostolische Kirche</div>
  <div class="header-title">Bibelwort</div>
  <div class="header-date">{{ wochentag }}, {{ heute }}</div>
</div>

{% for stufe, text in hinweise %}
<div class="banner feed-{{ stufe }}"><i class="ti ti-alert-triangle" style="font-size:16px;margin-top:1px"></i><span>{{ text }}</span></div>
{% endfor %}

<div class="card">
  <div class="card-header">
    <span class="card-title">Heutiger Gottesdienst</span>
    {% if eintrag_heute %}
      {% if eintrag_heute.quelle == 'manuell' %}<span class="badge badge-manuell">manuell</span>
      {% elif eintrag_heute.quelle == 'online' %}<span class="badge badge-online">online</span>
      {% elif eintrag_heute.quelle == 'pdf' %}<span class="badge badge-pdf">Leitgedanken</span>
      {% else %}<span class="badge badge-csv">CSV</span>{% endif %}
    {% else %}<span class="badge badge-leer">kein Eintrag</span>{% endif %}
  </div>
  <label>Bibelstelle</label>
  <div class="row">
    <input type="text" id="stelle_heute" value="{{ eintrag_heute.stelle if eintrag_heute else '' }}" placeholder="z. B. Johannes 3,16">
    <button class="btn btn-icon" onclick="ladeText()" title="Auf Bibleserver öffnen"><i class="ti ti-external-link"></i></button>
  </div>
  <label>Bibeltext</label>
  <textarea id="text_heute" placeholder="Text eingeben…">{{ eintrag_heute.text if eintrag_heute else '' }}</textarea>
  <div class="btn-group">
    <button class="btn btn-primary" onclick="speichereHeute()"><i class="ti ti-device-floppy"></i> Speichern</button>
    <button class="btn" onclick="druckeHeute()"><i class="ti ti-printer"></i> Drucken</button>
    <button class="btn" onclick="aktualisiereDisplay()"><i class="ti ti-refresh"></i> Display aktualisieren</button>
    {% if eintrag_heute and eintrag_heute.quelle == 'manuell' %}
    <button class="btn btn-danger" onclick="loescheManuelleHeute()"><i class="ti ti-x"></i> Löschen</button>
    {% endif %}
  </div>
  <div class="msg" id="msg_heute"></div>
</div>

<div class="card">
  <div class="card-header">
    <span class="card-title">Planung – 4 Wochen</span>
  </div>
  {% for tag in planung %}
  <div class="planrow">
    <div class="plan-date">
      {{ tag.wochentag }}, {{ tag.datum }}
      {% if tag.heute %}<span class="plan-heute">Heute</span>{% endif %}
    </div>
    <div>
      {% if tag.stelle %}
        <div class="plan-stelle">{{ tag.stelle }}</div>
        <div class="plan-text">{{ tag.text[:55] }}{% if tag.text|length > 55 %}…{% endif %}</div>
        <span class="badge {% if tag.quelle == 'manuell' %}badge-manuell{% elif tag.quelle == 'online' %}badge-online{% elif tag.quelle == 'pdf' %}badge-pdf{% else %}badge-csv{% endif %}" style="margin-top:3px">{{ 'Leitgedanken' if tag.quelle == 'pdf' else tag.quelle }}</span>
      {% else %}
        <span class="plan-empty">kein Eintrag</span>
      {% endif %}
    </div>
    <div style="display:flex;flex-direction:column;gap:4px">
      <button class="btn btn-sm"
        data-datum="{{ tag.datum }}"
        data-stelle="{{ tag.stelle | e }}"
        data-text="{{ tag.text | e }}"
        onclick="oeffneEditorBtn(this)"><i class="ti ti-edit"></i></button>
      {% if tag.quelle == 'manuell' %}
      <button class="btn btn-sm btn-danger" onclick="loescheManuell('{{ tag.datum }}')"><i class="ti ti-x"></i></button>
      {% endif %}
    </div>
  </div>
  {% endfor %}
  <div style="padding-top:12px">
    <button class="btn btn-sm" onclick="oeffneFreiesDatum()"><i class="ti ti-plus"></i> Freies Datum</button>
  </div>
</div>

<div class="card">
  <div class="card-header"><span class="card-title">Leitgedanken-PDF</span></div>
  <p class="hinweistext">Monatsheft „Leitgedanken zum Gottesdienst“ hochladen. Die Bibelworte werden automatisch ausgelesen und mit der Kalenderübersicht des Hefts abgeglichen. Vor dem Übernehmen siehst du eine Vorschau.</p>
  <input type="file" id="pdf_datei" accept="application/pdf,.pdf" style="font-size:12px">
  <div class="btn-group">
    <button class="btn btn-primary" onclick="pdfAuswerten()"><i class="ti ti-file-search"></i> PDF auswerten</button>
  </div>
  <div class="msg" id="msg_pdf"></div>
  {% if hefte %}<div class="feed-status feed-ok">Gespeichert: {{ hefte|join(', ') }} · die letzten {{ pdf_behalten }} Hefte bleiben erhalten</div>{% endif %}
</div>

<div class="grid2">
  <div class="card">
    <div class="card-header"><span class="card-title">Online-Feed</span></div>
    <input type="text" id="online_url" value="{{ online_url }}" placeholder="GitHub Pages URL" style="margin-bottom:8px">
    <div class="btn-group" style="margin-top:0">
      <button class="btn btn-sm" onclick="speichereOnlineUrl()"><i class="ti ti-device-floppy"></i> Speichern</button>
      <button class="btn btn-sm" onclick="aktualisiereJetzt()"><i class="ti ti-refresh"></i> Abrufen</button>
    </div>
    <div class="feed-status feed-{{ feed.level }}">{{ feed.text }}{% if feed.detail %}<span class="feed-detail">{{ feed.detail }}</span>{% endif %}</div>
    <div class="msg" id="msg_online"></div>
  </div>
  <div class="card">
    <div class="card-header"><span class="card-title">CSV hochladen</span></div>
    <p style="font-size:10px;color:#bbb;margin-bottom:8px">datum;stelle;text</p>
    <input type="file" id="csv_datei" accept=".csv" style="font-size:11px;margin-bottom:8px">
    <button class="btn btn-sm" onclick="ladeCSVhoch()"><i class="ti ti-upload"></i> Hochladen</button>
    <div class="msg" id="msg_csv"></div>
  </div>
</div>

<div class="card">
  <div class="card-header"><span class="card-title">WLAN</span></div>
  <button class="btn btn-sm" onclick="scanneWlan()" style="margin-bottom:8px"><i class="ti ti-wifi"></i> Netze scannen</button>
  <div class="scan-list" id="scan_liste"></div>
  <label>SSID</label>
  <input type="text" id="wlan_ssid" placeholder="Netzwerkname">
  <label>Passwort</label>
  <div class="row">
    <input type="password" id="wlan_pw" placeholder="Passwort">
    <button class="btn btn-icon" onclick="togglePw()"><i class="ti ti-eye"></i></button>
  </div>
  <div class="btn-group">
    <button class="btn btn-primary" onclick="verbindeWlan()"><i class="ti ti-plug-connected"></i> Verbinden</button>
  </div>
  <div class="msg" id="msg_wlan"></div>
</div>

<div class="modal-overlay" id="modal" onclick="if(event.target===this)schliesseModal()">
  <div class="modal-box">
    <div class="modal-title" id="modal_titel">Eintrag bearbeiten</div>
    <div id="modal_datum_zeile" style="display:none">
      <label>Datum (TT.MM.JJJJ)</label>
      <input type="text" id="modal_datum_input" placeholder="z. B. 25.12.2026" style="margin-bottom:8px">
    </div>
    <label>Bibelstelle</label>
    <div class="row">
      <input type="text" id="modal_stelle" placeholder="z. B. Römer 8,28">
      <button class="btn btn-icon" onclick="ladeTextModal()"><i class="ti ti-external-link"></i></button>
    </div>
    <label>Bibeltext</label>
    <textarea id="modal_text" style="min-height:110px"></textarea>
    <input type="hidden" id="modal_datum">
    <div class="btn-group">
      <button class="btn btn-primary" onclick="speichereModal()"><i class="ti ti-device-floppy"></i> Speichern</button>
      <button class="btn" onclick="schliesseModal()">Abbrechen</button>
    </div>
    <div class="msg" id="msg_modal"></div>
  </div>
</div>

<div class="modal-overlay" id="pdf_modal" onclick="if(event.target===this)schliessePdf()">
  <div class="modal-box" style="max-height:85vh;overflow-y:auto">
    <div class="modal-title" id="pdf_titel">Vorschau</div>
    <div id="pdf_pruefung"></div>
    <div id="pdf_liste" style="margin-top:8px"></div>
    <div class="btn-group">
      <button class="btn btn-primary" onclick="pdfUebernehmen()"><i class="ti ti-check"></i> Übernehmen</button>
      <button class="btn" onclick="schliessePdf()">Abbrechen</button>
    </div>
    <div class="msg" id="msg_pdfmodal"></div>
  </div>
</div>

<div class="footer">NAK Bibelwort &middot; Raspberry Pi Zero 2W</div>

<script>
function zeig(id,ok,text){const el=document.getElementById(id);el.textContent=text;el.className='msg '+(ok?'msg-ok':'msg-err');el.style.display='block';setTimeout(()=>el.style.display='none',4000)}
async function api(url,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return r.json()}
function ladeText(){const s=document.getElementById('stelle_heute').value.trim();if(!s)return;window.open('https://www.bibleserver.com/LUT/'+s.replace(/ /g,'').replace(/\\./g,''),'_blank')}
async function speichereHeute(){const stelle=document.getElementById('stelle_heute').value.trim(),text=document.getElementById('text_heute').value.trim();if(!text){zeig('msg_heute',false,'Bitte Text eingeben.');return}const r=await api('/api/speichern',{datum:'heute',stelle,text});zeig('msg_heute',r.ok,r.ok?'Gespeichert.':'Fehler.');if(r.ok)setTimeout(()=>location.reload(),1200)}
async function druckeHeute(){const stelle=document.getElementById('stelle_heute').value.trim(),text=document.getElementById('text_heute').value.trim();if(!text){zeig('msg_heute',false,'Kein Text.');return}zeig('msg_heute',true,'Druck läuft…');const r=await api('/api/drucken',{datum:'heute',stelle,text});zeig('msg_heute',r.ok,r.ok?'Druck erfolgreich.':'Druckfehler: '+(r.fehler||''))}
async function loescheManuelleHeute(){if(!confirm('Löschen?'))return;const r=await api('/api/loeschen',{datum:'heute'});if(r.ok)location.reload()}
function oeffneEditorBtn(btn){
  oeffneEditor(btn.getAttribute('data-datum'),btn.getAttribute('data-stelle'),btn.getAttribute('data-text'));
}
function oeffneEditor(datum,stelle,text){document.getElementById('modal_datum').value=datum;document.getElementById('modal_stelle').value=stelle;document.getElementById('modal_text').value=text;document.getElementById('modal_titel').textContent='Eintrag: '+datum;document.getElementById('modal_datum_zeile').style.display='none';document.getElementById('modal_datum_input').value='';document.getElementById('msg_modal').style.display='none';document.getElementById('modal').style.display='flex'}
function oeffneFreiesDatum(){document.getElementById('modal_datum').value='';document.getElementById('modal_stelle').value='';document.getElementById('modal_text').value='';document.getElementById('modal_titel').textContent='Freies Datum';document.getElementById('modal_datum_zeile').style.display='block';document.getElementById('modal_datum_input').value='';document.getElementById('msg_modal').style.display='none';document.getElementById('modal').style.display='flex'}
function schliesseModal(){document.getElementById('modal').style.display='none'}
function ladeTextModal(){const s=document.getElementById('modal_stelle').value.trim();if(!s)return;window.open('https://www.bibleserver.com/LUT/'+s.replace(/ /g,'').replace(/\\./g,''),'_blank')}
async function speichereModal(){let datum=document.getElementById('modal_datum').value;if(!datum){datum=document.getElementById('modal_datum_input').value.trim();if(!/^\\d{2}\\.\\d{2}\\.\\d{4}$/.test(datum)){zeig('msg_modal',false,'Datum TT.MM.JJJJ eingeben.');return}}const stelle=document.getElementById('modal_stelle').value.trim(),text=document.getElementById('modal_text').value.trim();if(!text){zeig('msg_modal',false,'Bitte Text eingeben.');return}const r=await api('/api/speichern',{datum,stelle,text});if(r.ok){schliesseModal();location.reload()}else zeig('msg_modal',false,'Fehler.')}
async function loescheManuell(datum){if(!confirm('Eintrag für '+datum+' löschen?'))return;const r=await api('/api/loeschen',{datum});if(r.ok)location.reload()}
async function speichereOnlineUrl(){const url=document.getElementById('online_url').value.trim();const r=await api('/api/online-url',{url});zeig('msg_online',r.ok,r.ok?'Gespeichert.':'Fehler.')}
async function aktualisiereJetzt(){zeig('msg_online',true,'Wird abgerufen…');const r=await api('/api/online-refresh',{});zeig('msg_online',r.ok,r.ok?r.meldung:(r.fehler||'Fehler'));setTimeout(()=>location.reload(),2500)}
async function ladeCSVhoch(){const datei=document.getElementById('csv_datei').files[0];if(!datei){zeig('msg_csv',false,'Keine Datei.');return}const fd=new FormData();fd.append('datei',datei);const r=await(await fetch('/api/csv-upload',{method:'POST',body:fd})).json();zeig('msg_csv',r.ok,r.ok?r.meldung:(r.fehler||'Fehler'));if(r.ok)setTimeout(()=>location.reload(),1500)}
function togglePw(){const f=document.getElementById('wlan_pw');f.type=f.type==='password'?'text':'password'}
async function scanneWlan(){
  const liste=document.getElementById('scan_liste');
  liste.style.display='block';
  liste.innerHTML='<div class="scan-item" style="color:#aaa">Scanne...</div>';
  const r=await api('/api/wlan-scan',{});
  if(r.ok&&r.netze.length>0){
    liste.innerHTML=r.netze.map(function(n){
      const ssid=n.ssid.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      return '<div class="scan-item" data-ssid="'+ssid+'" onclick="waehleNetz(this)">'+
             '<span>'+ssid+'</span><span class="scan-signal">'+n.signal+'%</span></div>';
    }).join('');
  } else {
    liste.innerHTML='<div class="scan-item" style="color:#aaa">Keine Netze gefunden.</div>';
  }
}
async function aktualisiereDisplay(){zeig('msg_heute',true,'Feed wird abgerufen, Display aktualisiert…');const r=await api('/api/display-refresh',{});zeig('msg_heute',r.ok,r.ok?r.meldung:(r.fehler||'Fehler'));if(r.ok)setTimeout(()=>location.reload(),1500)}
let pdfToken=null;
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
async function pdfAuswerten(){
  const datei=document.getElementById('pdf_datei').files[0];
  if(!datei){zeig('msg_pdf',false,'Bitte zuerst ein PDF auswählen.');return}
  zeig('msg_pdf',true,'PDF wird ausgewertet…');
  const fd=new FormData();fd.append('datei',datei);
  let r;
  try{r=await(await fetch('/api/pdf-vorschau',{method:'POST',body:fd})).json();}
  catch(e){zeig('msg_pdf',false,'Hochladen fehlgeschlagen.');return}
  if(!r.ok){zeig('msg_pdf',false,r.fehler||'Fehler');return}
  document.getElementById('msg_pdf').style.display='none';
  pdfToken=r.token;
  document.getElementById('pdf_titel').textContent=r.ausgabe+' · '+r.eintraege.length+' Gottesdienste'+(r.ersetzt?' (ersetzt die bisherige Version)':'');
  let p;
  if(r.warnungen.length){
    p='<div class="feed-status feed-warn"><b>Bitte prüfen:</b>'+r.warnungen.map(function(w){return '<span class="feed-detail">• '+esc(w)+'</span>';}).join('')+'</div>';
  }else{
    p='<div class="feed-status feed-ok">Alle Bibelstellen stimmen mit der Kalenderübersicht des Hefts überein.</div>';
  }
  document.getElementById('pdf_pruefung').innerHTML=p;
  document.getElementById('pdf_liste').innerHTML=r.eintraege.map(function(e){
    let marken='';
    if(e.hinweis) marken+=' <span class="badge badge-leer">'+esc(e.hinweis)+'</span>';
    if(e.manuell) marken+=' <span class="badge badge-manuell">manueller Eintrag bleibt vorrangig</span>';
    return '<div class="vorschau-zeile"><div class="plan-date">'+esc(e.wochentag)+', '+esc(e.datum.slice(0,6))+'</div>'+
           '<div><div class="plan-stelle">'+esc(e.stelle)+marken+'</div><div class="vorschau-text">'+esc(e.text)+'</div></div></div>';
  }).join('');
  document.getElementById('msg_pdfmodal').style.display='none';
  document.getElementById('pdf_modal').style.display='flex';
}
function schliessePdf(){document.getElementById('pdf_modal').style.display='none';pdfToken=null;}
async function pdfUebernehmen(){
  if(!pdfToken)return;
  const r=await api('/api/pdf-uebernehmen',{token:pdfToken});
  if(r.ok){zeig('msg_pdfmodal',true,r.meldung);pdfToken=null;setTimeout(function(){location.reload();},1800);}
  else zeig('msg_pdfmodal',false,r.fehler||'Fehler');
}
function waehleNetz(el){document.getElementById('wlan_ssid').value=el.getAttribute('data-ssid');}
async function verbindeWlan(){const ssid=document.getElementById('wlan_ssid').value.trim(),pw=document.getElementById('wlan_pw').value;if(!ssid){zeig('msg_wlan',false,'Bitte SSID eingeben.');return}zeig('msg_wlan',true,'Verbinde…');const r=await api('/api/wlan',{ssid,pw});zeig('msg_wlan',r.ok,r.ok?r.meldung:(r.fehler||'Fehler'))}
</script>
</body>
</html>"""

@app.route("/")
def index():
    heute = date.today(); ds = heute.strftime("%d.%m.%Y")
    wt = ["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"][heute.weekday()]
    return render_template_string(HTML_TEMPLATE, heute=ds, wochentag=wt,
        eintrag_heute=lade_eintrag(ds), planung=naechste_gottesdienste(),
        online_url=lade_config().get("online_url",""), feed=feed_info(),
        hinweise=hinweise(), hefte=[n for _, n in gespeicherte_hefte()], pdf_behalten=PDF_BEHALTEN)

@app.route("/api/speichern", methods=["POST"])
def api_speichern():
    d = request.get_json()
    datum = d.get("datum","").strip(); stelle = d.get("stelle","").strip(); text = d.get("text","").strip()
    if datum == "heute": datum = date.today().strftime("%d.%m.%Y")
    if not datum or not text: return jsonify({"ok": False})
    setze_manuell(datum, stelle, text)
    if datum == date.today().strftime("%d.%m.%Y"): aktualisiere_display(stelle, text, datum)
    return jsonify({"ok": True})

@app.route("/api/drucken", methods=["POST"])
def api_drucken():
    d = request.get_json(); datum = d.get("datum","heute"); stelle = d.get("stelle",""); text = d.get("text","")
    if datum == "heute": datum = date.today().strftime("%d.%m.%Y")
    if not text:
        e = lade_eintrag(datum)
        if not e: return jsonify({"ok": False, "fehler": "Kein Eintrag"})
        stelle = e.get("stelle",""); text = e.get("text","")
    return jsonify({"ok": drucke_bibelwort(stelle, text, datum)})

@app.route("/api/loeschen", methods=["POST"])
def api_loeschen():
    d = request.get_json(); datum = d.get("datum","").strip()
    if datum == "heute": datum = date.today().strftime("%d.%m.%Y")
    loesche_manuell(datum); return jsonify({"ok": True})

@app.route("/api/online-url", methods=["POST"])
def api_online_url():
    d = request.get_json(); cfg = lade_config()
    cfg["online_url"] = d.get("url","").strip(); speichere_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/online-refresh", methods=["POST"])
def api_online_refresh():
    ok = aktualisiere_online_feed()
    if ok: zeige_aktuelles()   # neues Wort sofort aufs Display, falls sich etwas geändert hat
    info = feed_info()
    if ok: return jsonify({"ok": True, "meldung": info["text"]})
    return jsonify({"ok": False, "fehler": info["detail"] or lade_feed_status().get("fehler") or "Fehler"})

_pdf_vorschau = {}   # token → {"pfad": …, "ergebnis": …}; nur die letzte Vorschau wird gehalten

@app.route("/api/pdf-vorschau", methods=["POST"])
def api_pdf_vorschau():
    """PDF annehmen, auswerten und die Vorschau zurückgeben – noch nichts übernehmen."""
    import secrets
    if "datei" not in request.files: return jsonify({"ok": False, "fehler": "Keine Datei."})
    inhalt = request.files["datei"].read()
    if not inhalt.startswith(b"%PDF"): return jsonify({"ok": False, "fehler": "Das ist keine PDF-Datei."})
    for name in os.listdir(PDF_ORDNER):            # alte, nicht übernommene Vorschauen entfernen
        if name.startswith("_vorschau_"):
            try: os.remove(os.path.join(PDF_ORDNER, name))
            except OSError: pass
    token = secrets.token_hex(8)
    pfad = os.path.join(PDF_ORDNER, f"_vorschau_{token}.pdf")
    with open(pfad, "wb") as f: f.write(inhalt)
    try:
        erg = werte_leitgedanken_aus(pfad)
    except RuntimeError as e:
        os.remove(pfad); return jsonify({"ok": False, "fehler": str(e)})
    manuell = lade_manuell()
    for e in erg["eintraege"]: e["manuell"] = e["datum"] in manuell
    _pdf_vorschau.clear(); _pdf_vorschau[token] = {"pfad": pfad, "ergebnis": erg}
    ersetzt = erg["schluessel"] in [s for s, _ in gespeicherte_hefte()]
    return jsonify({"ok": True, "token": token, "ersetzt": ersetzt, **erg})

@app.route("/api/pdf-uebernehmen", methods=["POST"])
def api_pdf_uebernehmen():
    v = _pdf_vorschau.pop((request.get_json() or {}).get("token"), None)
    if not v: return jsonify({"ok": False, "fehler": "Vorschau abgelaufen – bitte das PDF erneut auswählen."})
    erg = v["ergebnis"]
    # Einträge dieses Hefts komplett ersetzen (falls es schon einmal hochgeladen wurde)
    daten = {d: e for d, e in lade_leitgedanken().items() if e.get("heft") != erg["schluessel"]}
    for e in erg["eintraege"]:
        daten[e["datum"]] = {"stelle": e["stelle"], "text": e["text"],
                             "titel": e["titel"], "heft": erg["schluessel"]}
    speichere_leitgedanken(daten)
    os.replace(v["pfad"], os.path.join(PDF_ORDNER, f"{erg['schluessel']}.pdf"))
    geloescht = raeume_pdfs_auf()
    zeige_aktuelles()
    meldung = f"{len(erg['eintraege'])} Gottesdienste aus {erg['ausgabe']} übernommen."
    if geloescht: meldung += f" Ältere PDFs gelöscht: {', '.join(geloescht)}."
    return jsonify({"ok": True, "meldung": meldung})

@app.errorhandler(413)
def zu_gross(_):
    return jsonify({"ok": False, "fehler": "Die Datei ist zu groß (max. 25 MB)."}), 413

@app.route("/api/display-refresh", methods=["POST"])
def api_display_refresh():
    """Feed abrufen (falls erreichbar) und Display/LED mit dem aktuellen Wort neu schreiben."""
    feed_ok = aktualisiere_online_feed()
    if not zeige_aktuelles(force=True):
        return jsonify({"ok": False, "fehler": "Kein Bibelwort für heute hinterlegt."})
    hinweis = "" if feed_ok else " (Feed nicht erreichbar – lokale Daten verwendet)"
    return jsonify({"ok": True, "meldung": "Display wird aktualisiert." + hinweis})

@app.route("/api/wlan-scan", methods=["POST"])
def api_wlan_scan():
    import subprocess
    try:
        r = subprocess.run(["sudo","nmcli","-t","-f","SSID,SIGNAL","device","wifi","list"],
                           capture_output=True, text=True, timeout=15)
        netze = []; seen = set()
        for zeile in r.stdout.splitlines():
            teile = zeile.split(":")
            if len(teile) >= 2 and teile[0].strip() and teile[0].strip() not in seen:
                seen.add(teile[0].strip())
                netze.append({"ssid": teile[0].strip(), "signal": teile[1].strip() if teile[1].strip().isdigit() else "0"})
        netze.sort(key=lambda x: int(x["signal"]), reverse=True)
        return jsonify({"ok": True, "netze": netze})
    except Exception as e: return jsonify({"ok": False, "fehler": str(e)})

@app.route("/api/wlan", methods=["POST"])
def api_wlan():
    import subprocess
    d = request.get_json(); ssid = d.get("ssid","").strip(); pw = d.get("pw","")
    if not ssid: return jsonify({"ok": False, "fehler": "Keine SSID"})
    try:
        cmd = ["sudo","nmcli","device","wifi","connect",ssid]
        if pw: cmd += ["password", pw]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0: return jsonify({"ok": True, "meldung": f"Verbunden mit {ssid}"})
        return jsonify({"ok": False, "fehler": r.stderr.strip() or r.stdout.strip()})
    except Exception as e: return jsonify({"ok": False, "fehler": str(e)})

@app.route("/api/csv-upload", methods=["POST"])
def api_csv_upload():
    if "datei" not in request.files: return jsonify({"ok": False, "fehler": "Keine Datei"})
    inhalt = request.files["datei"].read().decode("utf-8", errors="replace")
    try:
        neu = {}; anzahl = 0
        for z in csv.DictReader(StringIO(inhalt), delimiter=";"):
            d = z.get("datum","").strip()
            if d: neu[d] = {"stelle": z.get("stelle","").strip(), "text": z.get("text","").strip()}; anzahl += 1
    except Exception as e: return jsonify({"ok": False, "fehler": str(e)})
    if not anzahl: return jsonify({"ok": False, "fehler": "Keine gültigen Einträge"})
    bestehend = lade_csv(); bestehend.update(neu); speichere_csv(bestehend)
    return jsonify({"ok": True, "meldung": f"{anzahl} Einträge importiert."})

@app.route("/api/bibeltext", methods=["POST"])
def api_bibeltext():
    return jsonify({"ok": False, "text": None})

if __name__ == "__main__":
    init_gpio()
    threading.Thread(target=pruefe_wlan_und_ap, daemon=True).start()
    starte_scheduler()

    def start_feed_und_display():
        # Display zunächst mit lokal vorhandenen Daten befüllen (sofort sichtbar)
        zeige_aktuelles()

        # Feed abrufen – mehrmals versuchen, falls Netz beim Boot noch nicht da ist.
        # Versuche nach 0, 10, 20, 40, 60, 90, 120 s (insg. ~6 min)
        import time
        wartezeiten = [10, 10, 20, 20, 30, 30, 60, 60, 60]
        for wartezeit in [0] + wartezeiten:
            if wartezeit: time.sleep(wartezeit)
            if aktualisiere_online_feed():
                # Erfolg – Display mit frischen Daten aktualisieren und fertig
                zeige_aktuelles()
                log.info("Online-Feed beim Start erfolgreich geladen.")
                return
        log.warning("Online-Feed beim Start nicht erreichbar – nutze lokale Daten.")
    threading.Thread(target=start_feed_und_display, daemon=True).start()

    app.run(host="0.0.0.0", port=80, debug=False)
