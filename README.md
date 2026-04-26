# JTL Wawi Product Import

Odoo-Modul zum Import von Produktstammdaten aus JTL-Wawi-CSV-Exports mit gestuftem Wizard und Hintergrundverarbeitung.

## Funktionen

- Mehrstufiger Import-Wizard für Dateiupload, Mapping und Validierung.
- Getrennte CSV-Tabs für Artikeldaten, Herstellerdaten, EU-Responsible-Daten, Kategorien, Lieferantenartikel, Variationen, Attribute, Merkmale und Stücklisten.
- Automatische Erkennung der CSV-Header und Zuordnung zu Odoo-Feldern.
- Barcode-Konfliktbehandlung mit Modus für Überspringen oder Match-Update.
- Verknüpfung von Eltern- und Kindartikeln für Varianten.
- Synchronisierung von Hersteller und EU Responsible.
- Markenverwaltung mit Odoo-Produktmarken.
- Import von Lieferanteninfos, Bildern, Bestand, SEO und Stücklisten.
- Längen-, Breiten- und Höhenwerte werden automatisch ins Volumen übernommen.
- Hintergrundverarbeitung in Batches mit Protokollen und Import-Historie.

## Enthaltene Dateien

- `static/description/icon.png`
- `static/description/main_1.png`
- `static/description/main_screenshot.png`
- `static/description/index.html`

## Hinweise

- Das Modul ist für Odoo 19.0 ausgelegt.
- Bereits vorhandene doppelte Barcodes werden ignoriert, sofern `Barcode Match Update` nicht aktiviert ist.
- Technische Legacy-Felder für GDPR-Kompatibilität bleiben im Modell erhalten, die Oberfläche verwendet jedoch nur noch `EU Responsible`.
