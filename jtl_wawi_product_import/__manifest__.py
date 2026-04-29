{
    "name": "JTL-Wawi Product Import",
    "version": "19.0.1.0.23",
    "category": "Inventory",
    "summary": "Import von JTL-Wawi CSV-Dateien mit gestufter Hintergrundverarbeitung.",
    "author": "Cognisance UG",
    "description": """
JTL Wawi Product Import bringt einen gestuften CSV-Import aus JTL-Wawi in Odoo.

Features:
- Mehrdateien-Import-Wizard für Artikeldaten, Herstellerdaten, EU-Responsible-Daten, Kategorien, Lieferantenartikel, Variationen, Attribute, Merkmale und Stücklisten.
- Automatische Zuordnung von CSV-Headern zu Odoo-Feldern mit Tabs je Datei.
- Barcode-Konfliktbehandlung mit Modus für Aktualisieren oder Überspringen.
- Verknüpfung von Eltern- und Kindartikeln, Synchronisierung von Lieferanteninfos, Marken, SEO-Feldern und Dimensionen zu Volumen.
- Hintergrundverarbeitung in Batches mit Protokollen und Import-Historie.
- Herstellerdaten und EU-RP-Daten werden als Marken gemappt und bei Bedarf automatisch mit angelegt.
""",
    "depends": ["account", "contacts", "mail", "product", "purchase_stock", "stock"],
    "data": [
        "security/ir.model.access.csv",
        "data/jtl_import_sequence.xml",
        "data/jtl_import_mapping_data.xml",
        "data/jtl_import_cron.xml",
        "views/jtl_import_mapping_views.xml",
        "views/jtl_import_profile_views.xml",
        "views/jtl_import_run_views.xml",
        "views/product_views.xml",
        "views/product_brand_views.xml",
        "views/res_partner_brand.xml",
        "views/product_brand_assign_wizard_views.xml",
        "wizard/jtl_import_wizard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "jtl_wawi_product_import/static/src/scss/jtl_import_wizard.scss",
        ],
    },
    "price": 0.0,
    "currency": "EUR",
    "images": [
        "static/description/main_1.png",
        "static/description/main_screenshot.png",
    ],
    "license": "OPL-1",
    "installable": True,
    "application": True,
}
