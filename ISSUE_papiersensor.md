# Issue: Papierende-Sensor löst Fehldruck / Druckabbruch aus

## Beschreibung

Der Goojprt QR203 besitzt einen optischen Papierende-Sensor (Paper-Out-Sensor). Bei der aktuellen Konfiguration meldet der Drucker fälschlicherweise „kein Papier" bzw. bricht den Druck ab, obwohl Papier eingelegt ist. Vermutlich reagiert der Sensor empfindlich auf Umgebungslicht oder die verwendete Papierqualität/-rückseite.

## Aktueller Workaround

Der Sensor wurde provisorisch mit einem **lichtundurchlässigen Klebestreifen** abgeklebt. Damit funktioniert der Druck zuverlässig. Nachteil: Die Papierende-Erkennung ist dadurch vollständig deaktiviert – ein leeres Papierfach wird nicht mehr gemeldet.

## Zu klären / nächste Schritte

- [ ] Passende Thermorollen beschaffen (58 mm, ggf. mit anderer Rückseite/Markierung) und testen, ob der Sensor mit „richtigem" Papier korrekt arbeitet
- [ ] Prüfen, ob der Sensor per ESC/POS-Statusabfrage ausgelesen werden kann, um den Papierstatus softwareseitig in der Weboberfläche / über die LED anzuzeigen
- [ ] Alternativ: dauerhafte, saubere Abdeckung statt Klebestreifen (z. B. im Gehäuse berücksichtigen)
- [ ] Entscheiden, ob die Papierende-Erkennung überhaupt benötigt wird oder der Workaround dauerhaft bleibt

## Technische Notizen

- Drucker: Goojprt QR203, 58 mm, USB (`0x28e9:0x0289`)
- Der Druck selbst funktioniert mit abgeklebtem Sensor einwandfrei (Grafikdruck via `GS v 0`, in 200-px-Streifen)
- Sensor sitzt im Papierschacht; abgeklebt = Drucker „sieht" immer Papier

## Priorität

Niedrig – Workaround ist stabil. Sauberere Lösung wünschenswert, sobald neues Papier beschafft und getestet ist.
