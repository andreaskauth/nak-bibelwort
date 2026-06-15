# NAK Bibelwort-Drucker

Ein eigenständiges Anzeige- und Drucksystem für das Bibelwort des Gottesdienstes, gebaut auf einem Raspberry Pi Zero 2W. Das aktuelle Bibelwort wird auf einem E-Paper-Display angezeigt, kann per Knopfdruck auf einem Thermodrucker ausgegeben und über eine Weboberfläche vom Smartphone aus konfiguriert werden.

Entwickelt für eine Gemeinde der Neuapostolischen Kirche – Gottesdienste mittwochs und sonntags (plus Feiertage). Das Projekt ist so aufgebaut, dass eine andere Gemeinde es mit überschaubarem technischem Aufwand nachbauen kann.

---

## Inhaltsverzeichnis

1. [Funktionen](#funktionen)
2. [Hardware-Einkaufsliste](#hardware-einkaufsliste)
3. [Verdrahtung](#verdrahtung)
4. [Installation Schritt für Schritt](#installation-schritt-für-schritt)
5. [Online-Feed einrichten (GitHub Pages)](#online-feed-einrichten-github-pages)
6. [Bedienung](#bedienung)
7. [Bibelwort-Hierarchie](#bibelwort-hierarchie)
8. [Fehlersuche](#fehlersuche)
9. [Projektdateien](#projektdateien)

---

## Funktionen

- **Weboberfläche** zur Pflege der Bibelworte, erreichbar im lokalen Netz oder über den eingebauten WLAN-Access-Point
- **Automatischer Online-Feed**: Bibelworte werden täglich von einer GitHub-Pages-JSON-Datei abgerufen
- **Manuelle Einträge** für einzelne Tage, die den Feed gezielt überschreiben
- **CSV-Upload** für die Planung mehrerer Wochen
- **Thermodruck** des Bibelwortes per Knopfdruck (zentriert, mit Umlauten)
- **E-Paper-Display** zeigt das aktuelle Wort dauerhaft an
- **WLAN-Fallback**: Startet automatisch einen Access Point, wenn kein bekanntes Netz erreichbar ist

---

## Hardware-Einkaufsliste

| Komponente | Modell / Hinweis | Richtpreis |
|---|---|---|
| Einplatinencomputer | Raspberry Pi Zero 2W (mit Stiftleiste) | ca. 20 € |
| microSD-Karte | 16 GB+ | ca. 6 € |
| Thermodrucker | Goojprt QR203, 58 mm, USB | ca. 30 € |
| Thermorollen | 58 mm (57 mm passt auch), Ø innen 12 mm, außen max. 40 mm | ca. 1 €/Rolle |
| Display | Waveshare e-Paper HAT (z. B. 2.13" oder 4.2" für größere Schrift) | 15–35 € |
| Taster | beliebiger Druckschalter | < 1 € |
| LED + Widerstand | LED + 220 Ω | < 1 € |
| Netzteil Pi | 5 V Micro-USB | ca. 8 € |
| Netzteil Drucker | 5–9 V, **mind. 2 A** (separat, nicht über den Pi!) | ca. 8 € |

> **Tipp:** Das Display möglichst als **HAT direkt aufstecken** statt einzeln zu verdrahten – das vermeidet Verpolungsfehler. Für ältere Gemeindemitglieder ist ein größeres Display (4.2") gut lesbar.

---

## Verdrahtung

### Drucker

Der Drucker wird per **USB** mit dem Pi verbunden (Datenkommunikation) und über ein **separates Netzteil** mit Strom versorgt. Der Pi-USB-Port allein liefert zu wenig Strom (ca. 500 mA), der Drucker zieht beim Druck bis zu 2 A.

USB-IDs des QR203: `0x28e9:0x0289`, Endpoints `out_ep=0x3`, `in_ep=0x81`.

### Taster und LED

| Bauteil | Anschluss |
|---|---|
| Taster | GPIO 27 (Pin 13) → Taster → 3,3 V (Pin 1) |
| LED | GPIO 22 (Pin 15) → 220 Ω → LED-Anode → LED-Kathode → GND (Pin 6) |

### Display (falls einzeln verdrahtet statt als HAT)

| Display | GPIO (BCM) | Pi-Pin |
|---|---|---|
| VCC | 3,3 V | Pin 1 |
| GND | GND | Pin 6 |
| DIN (MOSI) | GPIO 10 | Pin 19 |
| CLK (SCLK) | GPIO 11 | Pin 23 |
| CS | GPIO 8 | Pin 24 |
| DC | GPIO 25 | Pin 22 |
| RST | GPIO 17 | Pin 11 |
| BUSY | GPIO 24 | Pin 18 |
| PWR | GPIO 18 | Pin 12 |

> ⚠️ **Achtung Verpolung:** VCC niemals mit 5 V verbinden – das E-Paper verträgt nur 3,3 V und geht sonst kaputt. Pin 1 ist am quadratischen Lötpad erkennbar.

---

## Installation Schritt für Schritt

### 1. Raspberry Pi OS vorbereiten

Mit dem [Raspberry Pi Imager](https://www.raspberrypi.com/software/) **Raspberry Pi OS Lite (64-bit)** auf die SD-Karte schreiben. Im Imager unter den erweiterten Einstellungen vorab setzen:

- Hostname (z. B. `bibelwort`)
- Benutzername + Passwort
- SSH aktivieren
- WLAN-Zugangsdaten

### 2. Per SSH verbinden

```bash
ssh BENUTZER@bibelwort.local
```

### 3. Projekt klonen und installieren

```bash
git clone https://github.com/DEINNAME/nak-bibelwort.git
cd nak-bibelwort
bash setup.sh
```

Das Skript installiert alle Pakete, den Display-Treiber, richtet den Autostart-Dienst ein und aktiviert SPI.

### 4. Access Point konfigurieren (optional, empfohlen)

Den aktuellen WLAN-Kanal ermitteln:

```bash
iwlist wlan0 scan | grep -E "ESSID|Channel"
```

Die Beispieldateien kopieren und anpassen (Kanal eintragen!):

```bash
sudo cp hostapd.conf.example /etc/hostapd/hostapd.conf
sudo nano /etc/hostapd/hostapd.conf   # channel=11 ggf. anpassen

# dnsmasq-Zusatz anhängen
cat dnsmasq.conf.example | sudo tee -a /etc/dnsmasq.conf

sudo systemctl enable dnsmasq
```

### 5. Drucker testen

```bash
sudo python3 -c "
from escpos.printer import Usb
d = Usb(0x28e9, 0x0289, timeout=0, out_ep=0x3, in_ep=0x81)
d.text('Test\n\n\n'); d.cut(); d.close()
print('OK')
"
```

Erscheint der USB-Drucker nicht, mit `lsusb` die IDs prüfen und ggf. in `app.py` anpassen.

### 6. Fertig

Die Weboberfläche ist nun erreichbar unter `http://bibelwort.local`.

---

## Online-Feed einrichten (GitHub Pages)

Damit die Bibelworte automatisch geladen werden:

1. Dieses Repository als **öffentliches** Repo auf GitHub anlegen
2. Die `bibelworte.json` mit den eigenen Bibelworten pflegen (Format siehe unten)
3. Unter *Settings → Pages*: Source „Deploy from a branch", Branch `main`, Ordner `/ (root)`, **Save**
4. Nach 1–2 Minuten ist die Datei erreichbar unter:
   `https://DEINNAME.github.io/nak-bibelwort/bibelworte.json`
5. Diese URL in der Weboberfläche unter **Online-Feed** eintragen und speichern

**Format der `bibelworte.json`:**

```json
{
  "bibelworte": [
    {
      "datum": "03.05.2026",
      "stelle": "Apg 4,12",
      "text": "Und in keinem andern ist das Heil ..."
    }
  ]
}
```

> **Tipp:** JSON ist empfindlich bei Kommas. Vor dem Hochladen mit einem JSON-Validator prüfen – ein fehlendes Komma zwischen zwei Einträgen bricht den ganzen Feed.

Der Pi ruft den Feed beim Start und täglich um 3 Uhr automatisch ab.

---

## Bedienung

### Taste

- **Kurz drücken** → heutiges Bibelwort drucken
- **15 Sekunden halten** → WLAN-Access-Point ein-/ausschalten

### LED-Status

| LED | Bedeutung |
|---|---|
| Dauerlicht | druckbereit – Bibelwort für heute hinterlegt |
| aus | kein Bibelwort hinterlegt |
| kurz blinken | Druck läuft |
| schnell blinken | Fehler |
| langsam pulsieren | Access-Point-Modus aktiv |

### Access-Point-Modus

Wenn kein bekanntes WLAN erreichbar ist, startet der Pi nach ca. 15 Sekunden automatisch einen Access Point (oder per 15-Sekunden-Tastendruck):

- **WLAN:** `Bibelwort`
- **Passwort:** `gottesdienst`
- **Adresse:** `http://192.168.4.1`

Dort lässt sich unter „WLAN" das gewünschte Netz auswählen und verbinden.

---

## Bibelwort-Hierarchie

Für jedes Datum gilt:

1. **Manueller Eintrag** für genau dieses Datum (höchste Priorität)
2. Andernfalls der **neueste Eintrag** (CSV oder Online-Feed) mit Datum ≤ heute – dieser gilt weiter, bis ein neuerer Eintrag folgt

Ein manueller Eintrag überschreibt also nur seinen eigenen Tag und greift nicht dauerhaft durch. CSV und Online-Feed bilden die laufende Grundplanung.

In der Weboberfläche zeigt ein farbiger Badge die Quelle an: **manuell** (gelb), **CSV** (grün), **online** (blau).

---

## Fehlersuche

| Problem | Lösung |
|---|---|
| Drucker nicht gefunden | `lsusb` – USB-IDs prüfen und in `app.py` anpassen |
| „Resource busy" beim 2. Druck | `drucker.close()` muss nach jedem Druck aufgerufen werden (ist enthalten) |
| Umlaute fehlen im Druck | Druck erfolgt als Grafik (Pillow), nicht als Text – Schriftart `fonts-dejavu-core` installiert? |
| Display bleibt leer | SPI aktiviert? (`sudo raspi-config` → Interface → SPI); Verdrahtung prüfen |
| Access Point startet nicht | In `hostapd.conf` muss ein **fester Kanal** stehen (nicht `channel=0`) |
| Bibelwort beim Boot nicht aktuell | Pi braucht nach dem Start kurz, bis der Feed geladen ist – App holt Feed vor dem Display-Update |
| Papierende-Sensor meldet ständig „leer" | Siehe `docs/ISSUE_papiersensor.md` |

Logs des Dienstes ansehen:

```bash
sudo journalctl -u bibelwort -f
```

---

## Projektdateien

| Datei | Zweck |
|---|---|
| `app.py` | Die komplette Anwendung (Flask-Server, Druck, Display, GPIO, AP) |
| `setup.sh` | Installationsskript |
| `bibelworte.json` | Online-Feed (wird von GitHub Pages ausgeliefert) |
| `beispiel_planung.csv` | Beispiel für den CSV-Upload |
| `hostapd.conf.example` | Beispielkonfiguration Access Point |
| `dnsmasq.conf.example` | Beispielkonfiguration DHCP/DNS im AP-Modus |
| `docs/ISSUE_papiersensor.md` | Bekanntes Problem: Papierende-Sensor |

---

## Hinweis zur Bibeltext-Quelle

Die Lutherbibel (rev. 2017) ist urheberrechtlich geschützt und ohne offene API verfügbar. Der „Laden"-Button in der Weboberfläche öffnet daher die jeweilige Stelle auf bibleserver.com im Browser, von wo der Text manuell übernommen werden kann.

## Lizenz

Privates Gemeindeprojekt zur freien Nachnutzung. Bibeltexte: Lutherbibel rev. 2017 © 2016 Deutsche Bibelgesellschaft, Stuttgart.
