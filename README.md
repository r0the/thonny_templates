# thonny_templates

Thonny-Plugin, das Projektvorlagen aus dem Unterricht als ZIP-Datei von einer
der Vorlagenquellen (standardmässig
[teach.rothe.io](https://teach.rothe.io) und
[in.mygymer.ch](https://in.mygymer.ch)) lädt und in einen selbst gewählten
Ordner entpackt. Die Startdatei wird anschliessend in Thonny geöffnet.

## Installation

1. Die Wheel-Datei herunterladen:
   [thonny_templates-0.2.1-py3-none-any.whl](https://teach.rothe.io/informatik/thonny/thonny_templates-0.2.1-py3-none-any.whl)
2. In Thonny **Tools → Manage plug-ins…** öffnen.
3. Im Abschnitt **Install from local file** auf **here** klicken und die
   heruntergeladene Datei auswählen.
4. Thonny neu starten.

Eine frühere Installation des Plugins `thonny-vorlagen` muss zuerst entfernt
werden, sonst erscheint der Menübefehl doppelt.

Danach steht **Tools → Projektvorlagen…** zur Verfügung.

## Bedienung

- Unter **Quelle** die gewünschte Vorlagenliste auswählen.
- Vorlage in der Liste auswählen, Zielordner bei Bedarf mit **Ändern…**
  festlegen und **Herunterladen und öffnen** anklicken.
- Der Zielordner wird nach dem ersten Download gespeichert.
- Der Projektordner heisst wie die `id` der Vorlage (z. B. `schneefall`),
  erscheint in der Ansicht **Dateien** und die Startdatei öffnet sich im Editor.
- Die Projektseite unter der Liste öffnet sich mit einem Klick im Browser.
- Benötigte Pakete werden nach dem Entpacken automatisch geprüft und, falls sie
  fehlen, über Thonnys Paketverwaltung installiert.
- Existiert der Projektordner bereits, kann er überschrieben oder unter einem
  anderen Namen angelegt werden.

## Quellen

Standardmässig stehen zwei Vorlagenlisten zur Auswahl:

- `https://teach.rothe.io/thonny-templates.json`
- `https://in.mygymer.ch/thonny-templates.json`

Die Auswahl wird für den nächsten Start gespeichert. Weitere Quellen lassen sich
in der Thonny-Konfiguration eintragen (`sources` ist eine mit `;` getrennte
Liste von `Name|URL`):

```ini
[templates]
sources = Lokal|http://localhost:5173/thonny-templates.json
source = http://localhost:5173/thonny-templates.json
```

## Vorlagen anbieten

Die Vorlagenliste und die ZIP-Dateien liegen im Website-Repo `unterricht`:

- `web/docs/public/thonny-templates.json`
- `web/docs/public/informatik/...`

Ein Eintrag im Manifest genügt:

```json
{
  "id": "meine-vorlage",
  "title": "Meine Vorlage",
  "project_url": "informatik/24/meine-vorlage/",
  "url": "informatik/24/meine-vorlage.zip",
  "main": "meine-vorlage.py",
  "packages": ["pgturbo"]
}
```

`project_url` und `url` dürfen relativ zur Vorlagenliste sein. `project_url`
verweist auf die Projektseite im Browser, `main` ist optional. Die Vorlage wird
im Ordner `id` entpackt (im Beispiel `meine-vorlage`); die ZIP-Datei selbst
enthält keine Projektordner, sondern die Dateien direkt.

`packages` listet die benötigten Python-Pakete mit ihrem Namen auf PyPI (ohne
Versionsangabe). Nach dem Entpacken prüft das Plugin, ob sie im Interpreter von
Thonny vorhanden sind, und installiert fehlende Pakete automatisch über
Thonnys Paketverwaltung. Während ein Programm läuft, wird nicht installiert.

## Entwicklung

```sh
python3 -m pytest           # Tests
./build.sh                  # Wheel bauen und ins Website-Repo kopieren
```

`build.sh` kopiert das Wheel standardmässig nach
`../unterricht/web/docs/public/informatik/thonny`. Ein anderes Ziel lässt sich
mit `DEST=...` setzen.

Nach Änderungen am Plugin die Version in `pyproject.toml` erhöhen, neu bauen und
die Installationsseite im Website-Repo auf den neuen Dateinamen anpassen.
