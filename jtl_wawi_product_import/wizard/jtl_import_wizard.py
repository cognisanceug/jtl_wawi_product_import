import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import LazyTranslate

# Module-level dictionaries below capture translatable strings. The eager
# `_()` evaluates at import time when no language context exists yet
# (Odoo 19 logs a warning + full stack trace for every such call). Use
# LazyTranslate so the actual gettext lookup is deferred to first access.
_lt = LazyTranslate(__name__)


TARGET_MODEL_SELECTION = [
    ("product.template", "Product Template"),
    ("product.product", "Product Variant"),
    ("product.category", "Product Category"),
    ("product.public.category", "Website Category"),
    ("res.partner", "Partner"),
    ("product.supplierinfo", "Supplierinfo"),
    ("product.attribute", "Product Attribute"),
    ("product.attribute.value", "Attribute Value"),
    ("mrp.bom", "BOM"),
    ("mrp.bom.line", "BOM Line"),
    ("jtl.product.attribute.raw", "JTL Attribute Raw"),
    ("jtl.product.feature", "JTL Feature"),
    ("product.image", "Product Image"),
    ("account.tax", "Tax"),
    ("stock.quant", "Stock"),
    ("website", "Website"),
]

SOURCE_FILE_SELECTION = [
    ("article_master", "Artikelstammdaten"),
    ("manufacturer", "Hersteller"),
    ("eu_representative", "EU RP"),
    ("category", "Kategorien"),
    ("supplierinfo", "Lieferantenartikel"),
    ("supplier_master", "Lieferantenstammdaten"),
    ("variation_combination", "Variationskombinationen"),
    ("variation_definition", "Variationen"),
    ("attribute", "Attribute"),
    ("feature", "Eigene Merkmale"),
    ("bom", "Stueckliste"),
]

SOURCE_FILE_LABELS = dict(SOURCE_FILE_SELECTION)
TARGET_MODEL_LABELS = dict(TARGET_MODEL_SELECTION)

FILE_MODEL_WHITELIST = {
    "article_master": ["product.template", "product.product", "product.category", "res.partner", "product.supplierinfo", "product.public.category", "website", "account.tax", "stock.quant"],
    "manufacturer": ["res.partner"],
    "eu_representative": ["res.partner"],
    "category": ["product.category", "product.public.category", "website", "product.product", "product.template"],
    "supplierinfo": ["res.partner", "product.supplierinfo", "product.template", "product.product"],
    "supplier_master": ["res.partner"],
    "variation_combination": ["product.product", "product.template", "product.attribute", "product.attribute.value"],
    "variation_definition": ["product.attribute", "product.attribute.value", "product.template", "product.product"],
    "attribute": ["product.attribute", "product.attribute.value", "jtl.product.attribute.raw", "product.product", "product.template"],
    "feature": ["product.template", "jtl.product.feature", "product.product"],
    "bom": ["mrp.bom", "mrp.bom.line", "product.template", "product.product"],
}

SUPPORTED_CUSTOM_FIELD_TYPES = [
    ("char", "Char"),
    ("text", "Text"),
    ("html", "HTML"),
    ("integer", "Integer"),
    ("float", "Float"),
    ("boolean", "Boolean"),
    ("many2one", "Many2one"),
]

TTYPE_TRANSFORM_MAP = {
    "char": "trim",
    "text": "trim",
    "html": "html_clean",
    "integer": "integer",
    "float": "decimal_comma",
    "boolean": "boolean_normalize",
    "many2one": "trim",
}

TRANSFORM_SELECTION = [
    ("trim", "Trim"),
    ("uppercase", "Grossschreibung"),
    ("lowercase", "Kleinschreibung"),
    ("decimal", "Dezimal"),
    ("decimal_comma", "Dezimal-Komma zu Punkt"),
    ("html", "HTML"),
    ("html_clean", "HTML bereinigen"),
    ("boolean", "Boolean"),
    ("boolean_normalize", "Boolean normalisieren"),
    ("integer", "Integer"),
    ("slug", "Slug erzeugen"),
    ("path", "Kategoriepfad erzeugen"),
    ("char", "Zeichenkette"),
    ("selection", "Selection"),
    ("many2one", "Many2one"),
    ("ignore_empty", "Leere Werte ignorieren"),
    ("text", "Keine"),
]

CODE_LIKE_HEADERS = {
    "artikelnummer",
    "kindartikelnummer",
    "vaterartikelnummer",
    "gtin",
    "ean",
    "han",
    "tariccode",
    "artikelnummerlieferant",
    "lieferantennummer",
}

REQUIRED_COLUMN_ALIASES = {
    "article_master": [("artikelnummer", _lt("Artikelnummer")), ("artikelname", _lt("Artikelname"))],
    "variation_combination": [("kindartikelnummer", _lt("Kind Artikelnummer"))],
    "supplierinfo": [("lieferant", _lt("Lieferant"))],
    "supplier_master": [("firma", _lt("Firma"))],
    "bom": [("artikelnummerstuecklistenkomponente", _lt("Artikelnummer Stuecklistenkomponente"))],
}

# Columns that are read directly by the parser for internal linking and
# therefore must never be mapped to an Odoo field by the user. They are
# rendered as locked rows in the wizard so the user can see what the
# column is being used for.
RESERVED_COLUMN_KEYS = {
    "identifizierungsspaltevaterartikel": _lt("Reserved — used internally to link variant products to their parent article"),
    "istvaterartikel": _lt("Reserved — JTL parent flag, derived automatically from variant linkage"),
    "kategorieebene1": _lt("Reserved — read internally to build the category hierarchy (Kategorie Ebene 1–4)"),
    "kategorieebene2": _lt("Reserved — read internally to build the category hierarchy (Kategorie Ebene 1–4)"),
    "kategorieebene3": _lt("Reserved — read internally to build the category hierarchy (Kategorie Ebene 1–4)"),
    "kategorieebene4": _lt("Reserved — read internally to build the category hierarchy (Kategorie Ebene 1–4)"),
    "steuerklasse": _lt("Reserved — JTL tax class, translated internally to the matching sales tax (e.g. 'normaler steuersatz' → 19%)"),
    "steuerschluessel": _lt("Reserved — JTL tax class, translated internally to the matching sales tax (e.g. 'normaler steuersatz' → 19%)"),
    "warengruppe": _lt("Reserved — read internally as a single-level product category (inventory / e-commerce per import mode)"),
    "auflager": _lt("Reserved — read internally as the stock quantity (applied when 'Import Stock' is enabled)"),
    "bestand": _lt("Reserved — read internally as the stock quantity (applied when 'Import Stock' is enabled)"),
}


# Columns reserved only within a specific file, matched on the raw label.
# normalize_header_key() strips the "(Lieferant)" suffix that disambiguates
# these from the product's own columns, so they need a file-scoped exact match.
RESERVED_COLUMN_LABELS_BY_FILE = {
    "article_master": {
        "lieferant": _lt("Reserved — read internally as the product's supplier"),
        "artikelnummer (lieferant)": _lt("Reserved — read internally as the supplier product code"),
        "artikelname (lieferant)": _lt("Reserved — read internally as the supplier's product name"),
        "netto-ek": _lt("Reserved — read internally as the supplier purchase price"),
        "ust. in %": _lt("Reserved — read internally as the purchase tax (supplier tax)"),
        "lieferzeit in tagen (lieferant)": _lt("Reserved — read internally as the supplier delivery lead time"),
    },
}


def get_reserved_column_note(normalized_key, file_key=None, raw_label=None):
    """Reserved-column note for a header, or None. Besides the explicit keys
    above, the 'Global-Englisch: …' family is reserved (imported as the English
    translation), and some columns are reserved only within a specific file."""
    if normalized_key in RESERVED_COLUMN_KEYS:
        return RESERVED_COLUMN_KEYS[normalized_key]
    if normalized_key.startswith("globalenglisch"):
        return _("Reserved — imported as the English translation of the matching field")
    if normalized_key.startswith("jtlwawi"):
        return _("Reserved — imported as an Odoo pricelist (one pricelist per JTL price group)")
    if file_key and raw_label:
        note = RESERVED_COLUMN_LABELS_BY_FILE.get(file_key, {}).get(raw_label.strip().lower())
        if note:
            return note
    return None

# Scalar import settings stored on a mapping profile and restored when the
# profile is applied. category_root_id is a Many2one and handled separately.
PROFILE_SETTING_FIELDS = [
    "batch_size",
    "import_stock",
    "import_images",
    "import_gallery_images",
    "import_seo",
    "barcode_match_update",
    "update_existing_only",
    "dry_run",
    "manufacturer_create_brand",
    "category_import_mode",
]

# Hints rendered for pseudo target_field names that the processor
# resolves into a real Odoo relation during import. Shown in the
# 'Hinweis' column so the user understands what happens with the
# string value at import time.
PSEUDO_FIELD_HINTS = {
    "brand_name": _lt("→ Lookup product.brand by name (created if missing)"),
    "manufacturer_name": _lt("→ Lookup res.partner manufacturer by name (created if missing)"),
    "eu_responsible_name": _lt("→ Lookup res.partner EU representative by name (created if missing)"),
    "supplier_name": _lt("→ Lookup res.partner supplier by name (created with supplier_rank=1)"),
    "supplier_product_number": _lt("→ Stored as product.supplierinfo.product_code (supplier SKU)"),
    "manufacturer_external_id": _lt("→ Stored on res.partner.manufacturer_external_id; used for follow-up matches"),
    "eu_responsible_external_id": _lt("→ Stored on res.partner.eu_responsible_external_id; used for follow-up matches"),
    "brand_external_id": _lt("→ Stored on product.brand.brand_external_id; used for follow-up matches"),
    "category_path": _lt("→ Lookup/create product.category hierarchy from path"),
    "country_of_origin": _lt("→ Lookup res.country by name or ISO code"),
    "parent_sku": _lt("→ Variant linkage: matches parent product by SKU"),
    "gross_sales_price": _lt("→ Gross price; converted to list_price via tax_rate"),
    "tax_rate": _lt("→ Tax rate used for gross→net price conversion"),
}


def normalize_header_key(header):
    key = (header or "").strip().lower()
    key = key.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    key = re.sub(r"\([^)]*\)", "", key)
    key = re.sub(r"[^a-z0-9]+", "", key)
    return key


AUTO_MAPPING_ALIASES = {
    "article_master": {
        "artikelnummer": ("product.product", "default_code", "char"),
        "gtin": ("product.product", "barcode", "char"),
        "ean": ("product.product", "barcode", "char"),
        "artikelname": ("product.template", "name", "char"),
        "name": ("product.template", "name", "char"),
        "kurzbeschreibung": ("product.template", "description_sale", "text"),
        "beschreibung": ("product.template", "website_description", "html"),
        "bruttovk": ("product.template", "list_price", "float"),
        "nettoek": ("product.template", "standard_price", "float"),
        "artikelgewicht": ("product.template", "weight", "float"),
        "tariccode": ("product.template", "hs_code", "char"),
        "han": ("product.template", "x_jtl_han", "char"),
        "uvp": ("product.template", "compare_list_price", "float"),
        "hersteller": ("product.template", "x_jtl_manufacturer_id", "many2one"),
        "herkunftsland": ("product.template", "country_of_origin", "char"),
    },
    "supplierinfo": {
        "lieferant": ("res.partner", "name", "char"),
        "artikelnummerlieferant": ("product.supplierinfo", "product_code", "char"),
        "nettoek": ("product.supplierinfo", "price", "float"),
        "mindestabnahmelieferant": ("product.supplierinfo", "min_qty", "float"),
    },
    "bom": {
        "artikelnummer": ("mrp.bom", "product_id", "char"),
        "artikelnummerstuecklistenkomponente": ("mrp.bom.line", "product_id", "char"),
        "menge": ("mrp.bom.line", "product_qty", "float"),
    },
    "feature": {
        "wert": ("product.template", "x_jtl_feature_value", "char"),
    },
    "supplier_master": {
        "firma": ("res.partner", "name", "char"),
        "firmenzusatz": ("res.partner", "comment", "text"),
        "strasse": ("res.partner", "street", "char"),
        "adresszusatz": ("res.partner", "street2", "char"),
        "plz": ("res.partner", "zip", "char"),
        "ort": ("res.partner", "city", "char"),
        "landisolaendercode": ("res.partner", "country_id", "char"),
        "telefonzentrale": ("res.partner", "phone", "char"),
        "email": ("res.partner", "email", "char"),
        "webseite": ("res.partner", "website", "char"),
        "ustidnr": ("res.partner", "vat", "char"),
        "anmerkung": ("res.partner", "comment", "text"),
        "lieferantennummer": ("res.partner", "ref", "char"),
    },
}

CURATED_MODEL_FIELDS = {
    "product.template": ["name", "barcode", "weight", "list_price", "standard_price", "categ_id", "description_sale", "description", "website_description", "manufacturer_id", "manufacturer_partner_id", "manufacturer_sku", "hs_code", "country_of_origin", "brand_id", "sale_delay", "image_1920", "default_code", "is_storable", "type", "active", "sale_ok", "purchase_ok", "compare_list_price"],
    "product.product": ["default_code", "barcode", "weight", "parent_sku", "active"],
    "product.category": ["name", "parent_id"],
    "product.public.category": ["name", "parent_id"],
    "res.partner": ["name", "email", "website", "phone", "mobile", "street", "street2", "zip", "city", "country_id", "vat", "is_company", "is_manufacturer", "manufacturer_external_id", "is_eu_responsible", "eu_responsible_external_id", "comment", "ref", "image_1920"],
    "product.supplierinfo": ["partner_id", "product_tmpl_id", "product_id", "product_code", "price", "delay", "min_qty", "manufacturer_sku", "is_default_supplier"],
    "product.attribute": ["name", "create_variant"],
    "product.attribute.value": ["name", "attribute_id"],
    "mrp.bom": ["product_tmpl_id", "product_id", "code", "type"],
    "mrp.bom.line": ["bom_id", "product_id", "product_qty"],
    "account.tax": ["amount", "name", "type_tax_use"],
    "stock.quant": ["inventory_quantity", "location_id"],
}


class JtlImportWizard(models.TransientModel):
    _name = "jtl.import.wizard"
    _description = "JTL Import Wizard"
    _rec_name = "name"

    name = fields.Char(default=lambda self: _("New JTL Import"))
    article_master_file_name = fields.Char()
    article_master_file_data = fields.Binary(string="Artikeldaten")
    manufacturer_file_name = fields.Char()
    manufacturer_file_data = fields.Binary(string="Herstellerdaten")
    eu_representative_file_name = fields.Char()
    eu_representative_file_data = fields.Binary(string="EU-RP-Daten")
    category_file_name = fields.Char()
    category_file_data = fields.Binary(string="Kategoriedaten")
    supplierinfo_file_name = fields.Char()
    supplierinfo_file_data = fields.Binary(string="Lieferantenartikel")
    supplier_master_file_name = fields.Char()
    supplier_master_file_data = fields.Binary(string="Lieferantenstammdaten")
    variation_combination_file_name = fields.Char()
    variation_combination_file_data = fields.Binary(string="Variationskombinationen")
    variation_definition_file_name = fields.Char()
    variation_definition_file_data = fields.Binary(string="Variationsdefinitionen")
    attribute_file_name = fields.Char()
    attribute_file_data = fields.Binary(string="Attributdaten")
    feature_file_name = fields.Char()
    feature_file_data = fields.Binary(string="Merkmalsdaten")
    bom_file_name = fields.Char()
    bom_file_data = fields.Binary(string="Stueckliste")
    batch_size = fields.Integer(default=200, required=True)
    dry_run = fields.Boolean(default=False)
    update_existing_only = fields.Boolean(default=False, string="Update Existing Only")
    import_stock = fields.Boolean(default=False)
    import_images = fields.Boolean(default=True)
    import_gallery_images = fields.Boolean(default=False)
    import_seo = fields.Boolean(default=False)
    barcode_match_update = fields.Boolean(default=True, string="Barcode Match Update", help="When ticked the importer also matches existing products by barcode if no SKU match was found, before creating a new record.")
    manufacturer_create_brand = fields.Boolean(
        default=False,
        string="Hersteller als Marke importieren",
        help="Legt zu jedem importierten Hersteller eine gleichnamige Marke (product.brand) an und verknüpft sie direkt mit dem Hersteller-Kontakt.",
    )
    category_import_mode = fields.Selection(
        [
            ("both", "Lager- und E-Commerce-Kategorien"),
            ("inventory", "Nur Lagerkategorien"),
            ("ecommerce", "Nur E-Commerce-Kategorien"),
        ],
        string="Kategorien importieren als",
        default="both",
        required=True,
        help="Steuert, ob aus dem Kategorie-Pfad Lagerkategorien (product.category), "
        "E-Commerce-Kategorien (product.public.category) oder beide angelegt werden.",
    )
    import_limit = fields.Integer(
        default=0,
        string="Import-Limit (0 = alle)",
        help="Begrenzt die Anzahl der importierten Artikel — z.B. 100 für einen Test. "
        "0 bedeutet keine Begrenzung. Stammdaten (Hersteller, Kategorien) sind nicht betroffen.",
    )
    category_root_id = fields.Many2one(
        "product.category",
        string="Root Category",
        help="Optional: alle aus den JTL-Kategorien-Ebenen erzeugten Top-Level-Kategorien werden als Kinder dieser Odoo-Kategorie angelegt. Leer lassen, um direkt unter der Wurzel anzulegen.",
    )
    contacts_installed = fields.Boolean(compute="_compute_optional_module_status")
    website_sale_installed = fields.Boolean(compute="_compute_optional_module_status")
    product_variants_enabled = fields.Boolean(compute="_compute_optional_module_status")
    compare_list_price_enabled = fields.Boolean(compute="_compute_optional_module_status")
    precheck_message = fields.Html(compute="_compute_precheck_message", sanitize=False)
    profile_id = fields.Many2one("jtl.import.profile", string="Mapping Profile", domain="[('active', '=', True)]")
    save_profile = fields.Boolean(string="Save as Profile")
    profile_name = fields.Char(string="New Profile Name")
    profile_description = fields.Text()
    mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Field Mapping")
    article_master_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Artikelstammdaten", domain=[("source_file_key", "=", "article_master")])
    manufacturer_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Herstellerdaten", domain=[("source_file_key", "=", "manufacturer")])
    eu_representative_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="EU-RP-Daten", domain=[("source_file_key", "=", "eu_representative")])
    category_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Kategoriedaten", domain=[("source_file_key", "=", "category")])
    supplierinfo_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Lieferantenartikel", domain=[("source_file_key", "=", "supplierinfo")])
    supplier_master_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Lieferantenstammdaten", domain=[("source_file_key", "=", "supplier_master")])
    variation_combination_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Variationskombinationen", domain=[("source_file_key", "=", "variation_combination")])
    variation_definition_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Variationen", domain=[("source_file_key", "=", "variation_definition")])
    attribute_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Attribute", domain=[("source_file_key", "=", "attribute")])
    feature_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Merkmalsdaten", domain=[("source_file_key", "=", "feature")])
    bom_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Stueckliste", domain=[("source_file_key", "=", "bom")])
    headers_loaded = fields.Boolean(readonly=True)
    mapping_validated = fields.Boolean(readonly=True)
    wizard_step = fields.Selection([("files", "Files"), ("mapping", "Mapping"), ("review", "Review")], default="files", required=True)
    available_file_keys = fields.Char(
        compute="_compute_available_file_keys",
        help="Comma-wrapped list of source_file_keys that actually have mapping lines — "
        "used by the form view to hide mapping tabs for files that were not uploaded.",
    )

    @api.depends("mapping_line_ids.source_file_key")
    def _compute_available_file_keys(self):
        for wizard in self:
            keys = sorted(set(wizard.mapping_line_ids.mapped("source_file_key")))
            wizard.available_file_keys = ("," + ",".join(keys) + ",") if keys else ""

    @api.depends("import_seo")
    def _compute_optional_module_status(self):
        module_model = self.env["ir.module.module"].sudo()
        module_states = {
            record["name"]: record["state"]
            for record in module_model.search_read([("name", "in", ["contacts", "website_sale"])], ["name", "state"])
        }
        compare_list_price_available = "compare_list_price" in self.env["product.template"]._fields
        for wizard in self:
            wizard.contacts_installed = module_states.get("contacts") == "installed"
            wizard.website_sale_installed = module_states.get("website_sale") == "installed"
            wizard.product_variants_enabled = self.env.user.has_group("product.group_product_variant")
            wizard.compare_list_price_enabled = compare_list_price_available

    @api.depends("contacts_installed", "website_sale_installed", "product_variants_enabled", "compare_list_price_enabled", "import_seo", "category_import_mode", "category_file_data", "article_master_file_data", "variation_combination_file_data", "variation_definition_file_data")
    def _compute_precheck_message(self):
        for wizard in self:
            messages = []
            messages.append(_("`contacts` installed: %s") % (_("Yes") if wizard.contacts_installed else _("No")))
            messages.append(_("`website_sale` installed: %s") % (_("Yes") if wizard.website_sale_installed else _("No")))
            if wizard.category_file_data and not wizard.article_master_file_data:
                messages.append(_(
                    "Category file <strong>without</strong> article master: the category hierarchy is built and "
                    "assigned to <strong>existing</strong> products (matched by SKU). <strong>No new products are "
                    "created</strong> — import the article master file for that. The review step lists which "
                    "articles are matched."
                ))
            if wizard.category_import_mode == "inventory":
                messages.append(_(
                    "Categories: <strong>only inventory categories</strong> (<code>product.category</code>) "
                    "are imported."
                ))
            elif wizard.category_import_mode == "ecommerce":
                if wizard.website_sale_installed:
                    messages.append(_(
                        "Categories: <strong>only e-commerce categories</strong> "
                        "(<code>product.public.category</code>) are imported."
                    ))
                else:
                    messages.append(_(
                        "Categories: e-commerce only is selected, but `website_sale` is not installed "
                        "— the precheck will block the import."
                    ))
            else:
                if wizard.website_sale_installed:
                    messages.append(_(
                        "Categories: <strong>both</strong> inventory categories (<code>product.category</code>) "
                        "and e-commerce categories (<code>product.public.category</code>) are imported."
                    ))
                else:
                    messages.append(_(
                        "Categories: <strong>only inventory categories</strong> (<code>product.category</code>) "
                        "are imported — e-commerce categories are skipped because `website_sale` is not installed."
                    ))
            messages.append(_("Product variants enabled: %s") % (_("Yes") if wizard.product_variants_enabled else _("No")))
            if wizard.compare_list_price_enabled:
                messages.append(_("UVP / Compare Price (compare_list_price) available: Yes"))
            else:
                messages.append(_(
                    "UVP / Compare Price (compare_list_price) available: No "
                    "— enable it under Settings → Website → 'Show suggested retail price'"
                ))
            duplicate_detection = _(
                "Duplicate detection: existing records are <strong>updated, not duplicated</strong>. "
                "Lookup keys per model:"
                "<ul style='margin:4px 0 0 16px;'>"
                "<li><code>product.template / product.product</code>: SKU (default_code), then barcode%s</li>"
                "<li><code>res.partner</code> manufacturer / EU representative: name (case-insensitive), then external_id</li>"
                "<li><code>product.brand</code>: name (case-insensitive), then external_id</li>"
                "<li><code>product.category</code>: name + parent_id, hierarchy walked level by level</li>"
                "<li><code>product.supplierinfo</code>: partner + product + supplier_product_number</li>"
                "</ul>"
            ) % (_(" (active)") if wizard.barcode_match_update else _(" (disabled — enable Barcode Match Update)"))
            wizard.precheck_message = (
                "<div class='alert alert-info' role='alert'><strong>%s</strong><br/>- %s</div>"
                "<div class='alert alert-success' role='alert' style='margin-top:8px;'><strong>%s</strong><br/>%s</div>"
            ) % (
                _("Module Precheck"),
                "<br/>- ".join(messages),
                _("Update vs. Create"),
                duplicate_detection,
            )

    def _run_module_precheck(self):
        self.ensure_one()
        if self.import_seo and not self.website_sale_installed:
            raise UserError(_("SEO import is enabled, but `website_sale` is not installed."))
        if self.category_import_mode == "ecommerce" and not self.website_sale_installed:
            raise UserError(_("Category import mode is set to e-commerce only, but `website_sale` is not installed."))
        if (self.variation_combination_file_data or self.variation_definition_file_data) and not self.product_variants_enabled:
            raise UserError(_("Variation files were uploaded, but Odoo product variants are not enabled."))

    def _get_import_files(self):
        self.ensure_one()
        file_specs = []
        for file_key, _label in SOURCE_FILE_SELECTION:
            data = getattr(self, "%s_file_data" % file_key)
            filename = getattr(self, "%s_file_name" % file_key)
            if data:
                file_specs.append({"file_key": file_key, "file_name": filename or ("%s.csv" % file_key), "file_data": data})
        return file_specs

    def _get_default_mapping_lookup(self, file_key, column_keys, column_labels):
        def _add_mapping(target, line):
            target.setdefault(line.source_column, line)
            label = getattr(line, "source_column_label", False) or line.source_column
            if label and label not in target:
                target[label] = line

        lookup = {}
        # Profile lines take precedence — they are added first and _add_mapping
        # uses setdefault, so the built-in defaults below cannot overwrite them.
        if self.profile_id:
            for line in self.profile_id.line_ids.filtered(lambda l: l.active and l.source_file_key == file_key):
                _add_mapping(lookup, line)
        # Built-in defaults still fill in any columns the profile does not cover
        # (e.g. the attribute/feature file when the profile was saved from an
        # article-only import).
        default_mappings = self.env["jtl.import.mapping"].search([("active", "=", True), ("source_file_key", "=", file_key)], order="sequence, id")
        for mapping in default_mappings:
            _add_mapping(lookup, mapping)
        for label in column_labels:
            if label in lookup:
                continue
            # Loose, normalized matching is only safe for plain column names.
            # A label with a "(...)" qualifier (e.g. "Kurzbeschreibung (Druck/
            # Mailen/Faxen)") must match a default mapping exactly — otherwise
            # several distinct JTL columns collide onto the same target field.
            if "(" in (label or ""):
                continue
            normalized = normalize_header_key(label)
            for mapping in default_mappings:
                if normalize_header_key(mapping.source_column) == normalized:
                    lookup[label] = mapping
                    break
        return lookup

    def _guess_field_type(self, column_label, values, sample_value):
        normalized_label = normalize_header_key(column_label)
        if normalized_label in CODE_LIKE_HEADERS:
            return "char"
        values = [value for value in (values or []) if value not in (None, False, "")]
        sample_text = (sample_value or "").strip()
        if sample_text and re.search(r"<[^>]+>", sample_text):
            return "html"
        if values and all((value or "").strip().lower() in {"ja", "nein", "1", "0", "true", "false", "yes", "no", "x"} for value in values):
            return "boolean"
        if values and all(re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()) for value in values if value.strip()):
            return "char"
        if values and all(re.match(r"^-?\d+(?:[.,]\d+)?$", value.replace(" ", "")) for value in values if value.strip()):
            if any("," in value or "." in value for value in values):
                return "float"
            if normalized_label in CODE_LIKE_HEADERS or any(value.startswith("0") and len(value) > 1 for value in values):
                return "char"
            return "integer"
        if len(sample_text) > 120:
            return "text"
        return "char"

    def _guess_transform_logic(self, ttype):
        return TTYPE_TRANSFORM_MAP.get(ttype, "trim")

    def _guess_mapping_for_column(self, file_key, column_label, sample_value, values):
        normalized = normalize_header_key(column_label)
        # Columns with a "(...)" qualifier are JTL output-context / language
        # variants (e.g. "Kurzbeschreibung (Druck/Mailen/Faxen)") — they must
        # not be auto-guessed to the plain field, or several columns would
        # target the same field. They only map via an exact default mapping.
        alias = None if "(" in (column_label or "") else AUTO_MAPPING_ALIASES.get(file_key, {}).get(normalized)
        guessed_ttype = self._guess_field_type(column_label, values, sample_value)
        if not alias:
            return {"custom_field_ttype": guessed_ttype, "transform_logic": self._guess_transform_logic(guessed_ttype)}
        target_model, target_field_name, default_ttype = alias
        target_field_name = target_field_name.strip()
        guessed_ttype = default_ttype or guessed_ttype
        target_field_id = False
        if target_model != "website":
            target_field = self.env["ir.model.fields"].search([("model", "=", target_model), ("name", "=", target_field_name)], limit=1)
            target_field_id = target_field.id if target_field else False
        values = {
            "target_model": target_model,
            "target_field_id": target_field_id,
            "target_field_name": target_field_name,
            "custom_field_ttype": guessed_ttype,
            "transform_logic": self._guess_transform_logic(guessed_ttype),
            "import_enabled": True,
            "active": True,
        }
        if target_field_name.startswith("x_") and not target_field_id:
            values["create_field_if_missing"] = True
            values["new_field_name"] = target_field_name
            values["new_field_label"] = column_label
        return values

    def _build_mapping_lines(self, analysis_by_file):
        line_commands = [(5, 0, 0)]
        sequence = 1
        for file_key, _label in SOURCE_FILE_SELECTION:
            analysis_lines = analysis_by_file.get(file_key) or []
            if not analysis_lines:
                continue
            required_aliases = {alias for alias, _label in REQUIRED_COLUMN_ALIASES.get(file_key, [])}
            mapping_by_key = self._get_default_mapping_lookup(
                file_key,
                [item["source_column"] for item in analysis_lines],
                [item["source_column_label"] for item in analysis_lines],
            )
            for item in analysis_lines:
                mapping = mapping_by_key.get(item["source_column"]) or mapping_by_key.get(item["source_column_label"])
                guessed = {} if mapping else self._guess_mapping_for_column(file_key, item["source_column_label"], item["sample_value"], item.get("values"))
                target_field_id = False
                target_field_name = False
                if mapping:
                    target_field_name = mapping.target_field
                    if mapping.target_model != "website":
                        target_field = self.env["ir.model.fields"].search([("model", "=", mapping.target_model), ("name", "=", mapping.target_field)], limit=1)
                        target_field_id = target_field.id if target_field else False
                normalized_key = normalize_header_key(item["source_column_label"] or item["source_column"])
                is_reserved = get_reserved_column_note(
                    normalized_key, file_key, item["source_column_label"] or item["source_column"]
                ) is not None
                create_field_value = False if is_reserved else (
                    getattr(mapping, "create_field", False) if mapping else guessed.get("create_field_if_missing", False)
                )
                if is_reserved:
                    new_field_name_value = False
                    new_field_label_value = False
                elif mapping:
                    new_field_name_value = getattr(mapping, "new_field_name", False)
                    new_field_label_value = getattr(mapping, "new_field_label", False)
                else:
                    new_field_name_value = guessed.get("new_field_name", False)
                    new_field_label_value = guessed.get("new_field_label", False)
                # Only prefill the field-name columns when a new field is
                # actually going to be created — keep plain "Ignored" rows blank.
                if create_field_value and not new_field_label_value:
                    new_field_label_value = item["source_column_label"]
                values = {
                    "sequence": sequence * 10,
                    "source_file_key": file_key,
                    "source_column": item["source_column"],
                    "source_column_label": item["source_column_label"],
                    "sample_value": item["sample_value"],
                    "detected_ttype": self._guess_field_type(item["source_column_label"], item.get("values"), item["sample_value"]),
                    "target_model": False if is_reserved else (mapping.target_model if mapping else guessed.get("target_model")),
                    "target_field_id": False if is_reserved else (target_field_id or guessed.get("target_field_id")),
                    "target_field_name": False if is_reserved else (target_field_name or guessed.get("target_field_name")),
                    "custom_field_ttype": getattr(mapping, "new_field_type", False) or guessed.get("custom_field_ttype", "char"),
                    "transform_logic": mapping.transform_logic if mapping else guessed.get("transform_logic", "trim"),
                    "required": bool(getattr(mapping, "required", False)) or normalized_key in required_aliases,
                    "active": True,
                    "import_enabled": False if is_reserved else (getattr(mapping, "import_enabled", True) if mapping else bool(guessed.get("target_model") or guessed.get("active") or guessed.get("import_enabled"))),
                    "is_reserved": is_reserved,
                    "default_value": getattr(mapping, "default_value", False) if mapping else False,
                    "create_field_if_missing": create_field_value,
                    "new_field_name": new_field_name_value,
                    "new_field_label": new_field_label_value,
                    "relation_model": False if is_reserved else (getattr(mapping, "relation_model", False) if mapping else False),
                }
                line_commands.append((0, 0, values))
                sequence += 1
        self.mapping_line_ids = line_commands
        self.headers_loaded = True
        self.mapping_validated = False
        self.wizard_step = "mapping"

    def _reopen_wizard(self):
        return {"type": "ir.actions.act_window", "name": _("New JTL Import"), "res_model": "jtl.import.wizard", "res_id": self.id, "view_mode": "form", "target": "current"}

    def action_load_columns(self):
        self.ensure_one()
        self._run_module_precheck()
        file_specs = self._get_import_files()
        if not file_specs:
            raise UserError(_("Please upload at least one CSV file first."))
        analysis_by_file = self.env["jtl.import.parser"].analyze_bundle(file_specs)
        self._build_mapping_lines(analysis_by_file)
        return self._reopen_wizard()

    def _apply_profile_settings(self):
        """Copy the import settings stored on the selected profile onto the
        wizard so applying a profile also restores Root Category, batch size
        and the import toggles — not only the mapping lines. Field presence is
        checked so a code/DB version skew can never break profile selection."""
        profile = self.profile_id
        if not profile:
            return
        vals = {
            field: profile[field]
            for field in PROFILE_SETTING_FIELDS
            if field in profile._fields and field in self._fields
        }
        if "category_root_id" in profile._fields:
            vals["category_root_id"] = profile.category_root_id.id or False
        if vals:
            self.update(vals)

    @api.onchange("profile_id")
    def _onchange_profile_id(self):
        if self.profile_id:
            self._apply_profile_settings()

    def action_apply_profile(self):
        self.ensure_one()
        if not self.profile_id:
            raise UserError(_("Please select a mapping profile first."))
        self._apply_profile_settings()
        return self.action_load_columns()

    def action_use_default_profile(self):
        self.ensure_one()
        default_profile = self.env["jtl.import.profile"].search(
            [("is_default", "=", True), "|", ("company_id", "=", False), ("company_id", "=", self.env.company.id)],
            limit=1,
        )
        if not default_profile:
            raise UserError(_("No default mapping profile was found."))
        self.profile_id = default_profile
        self._apply_profile_settings()
        return self.action_load_columns()

    def action_reset_mapping(self):
        self.ensure_one()
        self.mapping_line_ids = [(5, 0, 0)]
        self.headers_loaded = False
        self.mapping_validated = False
        self.wizard_step = "files"
        return self._reopen_wizard()

    def action_previous_step(self):
        self.ensure_one()
        steps = ["files", "mapping", "review"]
        self.wizard_step = steps[max(steps.index(self.wizard_step) - 1, 0)]
        return self._reopen_wizard()

    def action_next_step(self):
        self.ensure_one()
        if self.wizard_step == "files":
            return self.action_load_columns()
        if self.wizard_step == "mapping":
            self.action_check_mapping()
            self.wizard_step = "review"
        return self._reopen_wizard()

    def _collect_profile_settings_vals(self):
        """Scalar import settings of the wizard, ready to write onto a profile."""
        vals = {"category_root_id": self.category_root_id.id or False}
        for field in PROFILE_SETTING_FIELDS:
            vals[field] = self[field]
        return vals

    def _collect_profile_line_commands(self):
        """(0, 0, {...}) commands describing the current mapping lines."""
        return [
            (
                0,
                0,
                {
                    "sequence": line.sequence,
                    "source_file_key": line.source_file_key,
                    "source_column": line.source_column,
                    "source_column_label": line.source_column_label,
                    "sample_value": line.sample_value,
                    "target_model": line.target_model,
                    "target_field": line.target_field_id.name or line.target_field_name,
                    "create_field": line.create_field_if_missing,
                    "new_field_name": line.new_field_name,
                    "new_field_label": line.new_field_label,
                    "new_field_type": line.custom_field_ttype,
                    "relation_model": line.relation_model,
                    "language_code": (line.language_id.code if line.language_id else line.language_code) or False,
                    "transform_logic": line.transform_logic,
                    "required": line.required,
                    "active": line.active,
                    "default_value": line.default_value,
                    "import_enabled": line.import_enabled,
                },
            )
            for line in self.mapping_line_ids
            if line.target_model and (line.target_field_id or line.target_field_name or line.new_field_name)
        ]

    def _save_profile_from_lines(self):
        self.ensure_one()
        if not self.save_profile:
            return False
        profile_name = (self.profile_name or "").strip()
        if not profile_name:
            raise UserError(_("Please enter a profile name if you want to save the mapping profile."))
        profile_vals = {
            "name": profile_name,
            "description": self.profile_description,
            "company_id": self.env.company.id,
            "line_ids": self._collect_profile_line_commands(),
        }
        profile_vals.update(self._collect_profile_settings_vals())
        # Reuse an existing profile with the same name instead of piling up
        # duplicates every time "Save as Profile" is ticked.
        existing = self.env["jtl.import.profile"].search(
            [
                ("name", "=", profile_name),
                "|",
                ("company_id", "=", self.env.company.id),
                ("company_id", "=", False),
            ],
            limit=1,
        )
        if existing:
            profile_vals["line_ids"] = [(5, 0, 0)] + profile_vals["line_ids"]
            existing.write(profile_vals)
            profile = existing
        else:
            profile = self.env["jtl.import.profile"].create(profile_vals)
        self.profile_id = profile
        return profile

    def action_update_profile(self):
        """Write the current wizard settings (and mapping lines, if loaded)
        back onto the already selected profile."""
        self.ensure_one()
        if not self.profile_id:
            raise UserError(_("Please select a mapping profile to update first."))
        update_vals = self._collect_profile_settings_vals()
        if self.profile_description:
            update_vals["description"] = self.profile_description
        # Only touch the mapping lines when they have actually been loaded —
        # otherwise an empty wizard would wipe the profile's mappings.
        if self.mapping_line_ids:
            update_vals["line_ids"] = [(5, 0, 0)] + self._collect_profile_line_commands()
        self.profile_id.write(update_vals)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Profile updated"),
                "message": _("Settings%s saved to profile '%s'.")
                % (_(" and mapping") if self.mapping_line_ids else "", self.profile_id.name),
                "type": "success",
                "sticky": False,
            },
        }

    def _check_required_mapping_aliases(self):
        present_file_keys = set(self.mapping_line_ids.mapped("source_file_key"))
        for file_key, requirements in REQUIRED_COLUMN_ALIASES.items():
            if file_key not in present_file_keys:
                continue
            file_lines = self.mapping_line_ids.filtered(lambda line: line.source_file_key == file_key)
            for alias, label in requirements:
                matched = file_lines.filtered(
                    lambda line: line.active
                    and line.import_enabled
                    and line.target_model
                    and (line.target_field_id or line.target_field_name)
                    and normalize_header_key(line.source_column_label or line.source_column) == alias
                )
                if not matched:
                    raise UserError(_("Pflichtmapping fehlt: %s wurde keinem Odoo-Feld zugeordnet.") % label)

    def _get_selected_mapping_specs(self):
        self.ensure_one()
        self.mapping_line_ids._ensure_dynamic_fields()
        specs = []
        for line in self.mapping_line_ids.filtered(lambda record: record.active and record.import_enabled):
            target_field = line.target_field_id.name or line.target_field_name
            if not line.target_model or not target_field:
                raise UserError(_("Please complete the mapping for column '%s'.") % (line.source_column_label or line.source_column))
            specs.append(
                {
                    "sequence": line.sequence,
                    "source_file_key": line.source_file_key,
                    "source_column": line.source_column,
                    "source_column_label": line.source_column_label,
                    "target_model": line.target_model,
                    "target_field": target_field,
                    "language_code": (line.language_id.code if line.language_id else line.language_code) or False,
                    "transform_logic": line.transform_logic,
                    "required": line.required,
                    "default_value": line.default_value or False,
                }
            )
        if not specs:
            raise UserError(_("Please activate and map at least one CSV column."))
        return specs

    def action_check_mapping(self):
        self.ensure_one()
        self.mapping_line_ids._validate_lines()
        self._check_required_mapping_aliases()
        self.mapping_line_ids._ensure_dynamic_fields()
        self.mapping_validated = True
        return self._reopen_wizard()

    def _build_assignment_only_preview(self, payload):
        """For SKUs that only come from assignment files (category / attribute /
        feature), produce a preview grouped by category path and by attribute
        name — one log entry per group, not per SKU — so the review step stays
        readable even for large files.

        Returns (logs, summary, skip_skus): skip_skus are assignment-only SKUs
        with no matching product. They are removed from the processing list so
        the import does not walk every row just to skip it."""
        products = payload.get("products", {}) or {}
        assignment_keys = {"category", "attribute", "feature"}
        assignment_only = {
            sku: record
            for sku, record in products.items()
            if record.get("source_file_keys")
            and set(record["source_file_keys"]).issubset(assignment_keys)
        }
        if not assignment_only:
            return [], "", []
        existing = self.env["product.product"].search([("default_code", "in", list(assignment_only.keys()))])
        existing_skus = set(existing.mapped("default_code"))
        skip_skus = [sku for sku in assignment_only if sku not in existing_skus]

        logs = []
        summary_parts = []

        # Categories — grouped by path.
        no_path_label = _("(no category path)")
        cat_groups = {}
        for sku, record in assignment_only.items():
            if "category" not in (record.get("source_file_keys") or []):
                continue
            category_path = (record.get("product") or {}).get("category_path") or no_path_label
            stats = cat_groups.setdefault(category_path, {"total": 0, "matched": 0})
            stats["total"] += 1
            if sku in existing_skus:
                stats["matched"] += 1
        for category_path, stats in sorted(cat_groups.items()):
            skipped = stats["total"] - stats["matched"]
            logs.append({
                "article_number": False,
                "field_name": "category_preview",
                "level": "info" if stats["matched"] else "warning",
                "message": _(
                    "Category '%(cat)s': %(total)s SKUs — %(matched)s existing products will be assigned, "
                    "%(skipped)s have no product yet (skipped)."
                ) % {"cat": category_path, "total": stats["total"], "matched": stats["matched"], "skipped": skipped},
            })
        if cat_groups:
            cat_total = sum(s["total"] for s in cat_groups.values())
            cat_matched = sum(s["matched"] for s in cat_groups.values())
            summary_parts.append(_(
                "Category file: %(total)s SKUs across %(groups)s categories — %(matched)s assigned, %(skipped)s skipped."
            ) % {"total": cat_total, "groups": len(cat_groups), "matched": cat_matched, "skipped": cat_total - cat_matched})

        # Attributes / features — grouped by attribute name.
        attr_groups = {}
        for sku, record in assignment_only.items():
            for attr in record.get("attributes") or []:
                name = (attr.get("name") or "").strip()
                if not name:
                    continue
                stats = attr_groups.setdefault(name, {"total": set(), "matched": set()})
                stats["total"].add(sku)
                if sku in existing_skus:
                    stats["matched"].add(sku)
        for name, stats in sorted(attr_groups.items()):
            total = len(stats["total"])
            matched = len(stats["matched"])
            logs.append({
                "article_number": False,
                "field_name": "attribute_preview",
                "level": "info" if matched else "warning",
                "message": _(
                    "Attribute/feature '%(name)s': %(total)s SKUs — %(matched)s existing products will get it, "
                    "%(skipped)s have no product yet (skipped)."
                ) % {"name": name, "total": total, "matched": matched, "skipped": total - matched},
            })
        if attr_groups:
            attr_skus = {sku for stats in attr_groups.values() for sku in stats["total"]}
            attr_matched = {sku for stats in attr_groups.values() for sku in stats["matched"]}
            summary_parts.append(_(
                "Attribute/feature files: %(names)s distinct attributes across %(total)s SKUs — "
                "%(matched)s SKUs matched, %(skipped)s skipped."
            ) % {
                "names": len(attr_groups),
                "total": len(attr_skus),
                "matched": len(attr_matched),
                "skipped": len(attr_skus) - len(attr_matched),
            })

        return logs, "\n".join(summary_parts), skip_skus

    def action_validate(self):
        self.ensure_one()
        self._run_module_precheck()
        file_specs = self._get_import_files()
        if not file_specs:
            raise UserError(_("Please upload at least one CSV file first."))
        if not self.mapping_line_ids:
            self.action_load_columns()
        self.action_check_mapping()
        self.wizard_step = "review"
        mapping_specs = self._get_selected_mapping_specs()
        self._save_profile_from_lines()
        run_filename = ", ".join(spec["file_name"] for spec in file_specs[:3])
        if len(file_specs) > 3:
            run_filename = _("%s (+%s more)") % (run_filename, len(file_specs) - 3)
        run = self.env["jtl.import.run"].create(
            {
                "filename": run_filename,
                "batch_size": self.batch_size,
                "dry_run": self.dry_run,
                "update_existing_only": self.update_existing_only,
                "import_stock": self.import_stock,
                "import_images": self.import_images,
                "import_gallery_images": self.import_gallery_images,
                "import_seo": self.import_seo,
                "barcode_match_update": self.barcode_match_update,
                "manufacturer_create_brand": self.manufacturer_create_brand,
                "category_import_mode": self.category_import_mode,
                "import_limit": self.import_limit,
                "category_root_id": self.category_root_id.id if self.category_root_id else False,
            }
        )
        run.set_source_bundle(file_specs)
        payload = self.env["jtl.import.parser"].parse_import_bundle(run, file_specs, mapping_specs=mapping_specs)
        parsed_sku_count = len(payload.get("skus", []))
        assignment_preview_logs, assignment_summary, skip_skus = self._build_assignment_only_preview(payload)
        # Drop assignment-only SKUs (category / attribute / feature) without a
        # matching product from the processing list — the category hierarchy is
        # built separately and attributes can only attach to existing products,
        # so there is no reason to walk these rows batch by batch.
        if skip_skus:
            skip_set = set(skip_skus)
            payload["skus"] = [sku for sku in payload.get("skus", []) if sku not in skip_set]
            for sku in skip_set:
                payload.get("products", {}).pop(sku, None)
        # Optional import limit — keep only the first N articles (for test runs).
        limit_note = ""
        if self.import_limit and self.import_limit > 0:
            kept = payload.get("skus", [])[: self.import_limit]
            if len(kept) < len(payload.get("skus", [])):
                dropped = set(payload.get("skus", [])) - set(kept)
                for sku in dropped:
                    payload.get("products", {}).pop(sku, None)
                limit_note = _("Import limit active: only the first %s articles are imported.") % self.import_limit
            payload["skus"] = kept
        run.set_payload(payload)
        validation_message = _("%s product groups from %s file(s) are staged and ready for queueing.") % (parsed_sku_count, len(file_specs))
        if assignment_summary:
            validation_message = "%s\n%s" % (validation_message, assignment_summary)
        if limit_note:
            validation_message = "%s\n%s" % (validation_message, limit_note)
        run.write(
            {
                "state": "validated",
                "validation_message": validation_message,
            }
        )
        if payload.get("warnings"):
            run._append_logs(payload["warnings"])
        if assignment_preview_logs:
            run._append_logs(assignment_preview_logs)
        return {"type": "ir.actions.act_window", "name": _("JTL Import Run"), "res_model": "jtl.import.run", "res_id": run.id, "view_mode": "form", "target": "current"}

    def action_validate_and_queue(self):
        action = self.action_validate()
        run = self.env["jtl.import.run"].browse(action["res_id"])
        run.action_queue()
        return action


class JtlImportWizardLine(models.TransientModel):
    _name = "jtl.import.wizard.line"
    _description = "JTL Import Wizard Line"
    _order = "source_file_key, sequence, id"

    wizard_id = fields.Many2one("jtl.import.wizard", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    source_file_key = fields.Selection(SOURCE_FILE_SELECTION, required=True, default="article_master", readonly=True)
    source_column = fields.Char(required=True, readonly=True)
    source_column_label = fields.Char(readonly=True)
    sample_value = fields.Char(readonly=True)
    target_model = fields.Selection(TARGET_MODEL_SELECTION)
    source_file_label = fields.Char(compute="_compute_source_file_label")
    available_model_keys = fields.Char(compute="_compute_available_model_keys")
    available_field_ids = fields.Many2many("ir.model.fields", compute="_compute_available_field_ids")
    target_field_id = fields.Many2one("ir.model.fields", string="Odoo Field", domain="[('id', 'in', available_field_ids)]")
    target_field_name = fields.Char(string="Target Field")
    create_field_if_missing = fields.Boolean(string="Create Field")
    new_field_name = fields.Char()
    new_field_label = fields.Char()
    relation_model = fields.Char()
    relation_hint = fields.Char(compute="_compute_relation_hint")
    target_model_label = fields.Char(compute="_compute_target_model_label")
    detected_ttype = fields.Selection(SUPPORTED_CUSTOM_FIELD_TYPES, string="Detected Field Type")
    custom_field_ttype = fields.Selection(SUPPORTED_CUSTOM_FIELD_TYPES, default="char")
    language_code = fields.Char()
    language_id = fields.Many2one("res.lang", string="Sprache", compute="_compute_language_id", inverse="_inverse_language_id", readonly=False, domain=[("active", "=", True)])
    transform_logic = fields.Selection(TRANSFORM_SELECTION, default="trim", required=True)
    required = fields.Boolean(default=False)
    active = fields.Boolean(default=False)
    import_enabled = fields.Boolean(default=True)
    is_reserved = fields.Boolean(
        default=False,
        readonly=True,
        help="If set, this column is reserved for internal use (e.g. parent article linking) and cannot be mapped to an Odoo field.",
    )
    default_value = fields.Char()
    field_required = fields.Boolean(compute="_compute_field_metadata")
    target_field_ttype = fields.Char(compute="_compute_field_metadata")
    status = fields.Selection([("valid", "Valid"), ("warning", "Warning"), ("error", "Error"), ("ignored", "Ignored"), ("reserved", "Ready")], compute="_compute_status")
    note = fields.Char(compute="_compute_status")

    @api.depends("source_file_key")
    def _compute_source_file_label(self):
        for line in self:
            line.source_file_label = SOURCE_FILE_LABELS.get(line.source_file_key, line.source_file_key)

    @api.depends("source_file_key")
    def _compute_available_model_keys(self):
        for line in self:
            line.available_model_keys = ",".join(FILE_MODEL_WHITELIST.get(line.source_file_key, []))

    @api.depends("target_model")
    def _compute_target_model_label(self):
        for line in self:
            line.target_model_label = TARGET_MODEL_LABELS.get(line.target_model, line.target_model or "")

    @api.onchange("target_field_id")
    def _onchange_target_field_id(self):
        for line in self:
            if line.target_field_id:
                line.target_field_name = line.target_field_id.name
                if line.target_field_id.ttype in dict(SUPPORTED_CUSTOM_FIELD_TYPES):
                    line.custom_field_ttype = line.target_field_id.ttype
                if line.target_field_id.ttype == "many2one":
                    line.relation_model = line.target_field_id.relation

    @api.onchange("source_column_label", "create_field_if_missing")
    def _onchange_source_column_generate_field(self):
        for line in self:
            if line.create_field_if_missing and not line.target_field_id and not line.new_field_name:
                sanitized = re.sub(r"[^a-z0-9_]+", "_", (line.source_column_label or line.source_column or "").strip().lower())
                sanitized = re.sub(r"_+", "_", sanitized).strip("_")
                if sanitized and not sanitized.startswith("x_"):
                    sanitized = "x_jtl_%s" % sanitized
                line.new_field_name = sanitized
                if not line.new_field_label:
                    line.new_field_label = line.source_column_label

    @api.onchange("target_model")
    def _onchange_target_model(self):
        for line in self:
            if line.target_model and line.target_model not in FILE_MODEL_WHITELIST.get(line.source_file_key, []):
                line.target_model = False
                line.target_field_id = False
                line.target_field_name = False
                return {
                    "warning": {
                        "title": _("Invalid model"),
                        "message": _("The selected model is not allowed for this CSV file type."),
                    }
                }

    @api.depends("target_model")
    def _compute_available_field_ids(self):
        ir_model_fields = self.env["ir.model.fields"]
        for line in self:
            if not line.target_model or line.target_model == "website":
                line.available_field_ids = [(6, 0, [])]
                continue
            allowed_names = set(CURATED_MODEL_FIELDS.get(line.target_model, []))
            base_domain = [("model", "=", line.target_model)]
            curated = ir_model_fields.search(base_domain + [("name", "in", list(allowed_names))]) if allowed_names else ir_model_fields.browse()
            custom = ir_model_fields.search(base_domain + [("name", "=like", "x_%")])
            line.available_field_ids = [(6, 0, (curated | custom).ids)]

    @api.depends("source_file_key", "source_column_label", "target_model", "target_field_id", "target_field_name")
    def _compute_relation_hint(self):
        for line in self:
            normalized = normalize_header_key(line.source_column_label or line.source_column)
            target_field_name = (line.target_field_name or "").strip()
            pseudo_hint = PSEUDO_FIELD_HINTS.get(target_field_name)
            if pseudo_hint:
                line.relation_hint = pseudo_hint
            elif line.source_file_key == "variation_combination" and normalized == "kindartikelnummer":
                line.relation_hint = _("Variant child SKU.")
            elif line.source_file_key == "bom" and normalized == "artikelnummerstuecklistenkomponente":
                line.relation_hint = _("BOM component reference.")
            elif line.source_file_key == "supplierinfo" and normalized == "lieferant":
                line.relation_hint = _("Supplier matched by name and created when missing.")
            elif line.target_field_id and line.target_field_id.ttype == "many2one":
                line.relation_hint = _("Relational value will be resolved or created during import.")
            else:
                line.relation_hint = False

    @api.depends("target_field_id")
    def _compute_field_metadata(self):
        for line in self:
            line.field_required = bool(line.target_field_id and line.target_field_id.required)
            line.target_field_ttype = line.target_field_id.ttype if line.target_field_id else False

    @api.depends("active", "import_enabled", "is_reserved", "source_column_label", "target_model", "target_field_id", "target_field_name", "create_field_if_missing", "new_field_name")
    def _compute_status(self):
        for line in self:
            if line.is_reserved:
                line.status = "reserved"
                normalized_key = normalize_header_key(line.source_column_label or line.source_column)
                line.note = get_reserved_column_note(
                    normalized_key, line.source_file_key, line.source_column_label or line.source_column
                ) or _("Reserved — used internally, not imported")
            elif not line.active or not line.import_enabled:
                line.status = "ignored"
                line.note = _("Ignored")
            elif line.create_field_if_missing and not line.new_field_name:
                line.status = "error"
                line.note = _("New field name is missing.")
            elif not line.target_model:
                line.status = "warning"
                line.note = _("Model missing.")
            elif not (line.target_field_id or line.target_field_name or line.new_field_name):
                line.status = "warning"
                line.note = _("Field missing.")
            else:
                line.status = "valid"
                line.note = _("Ready")

    @api.depends("language_code")
    def _compute_language_id(self):
        Lang = self.env["res.lang"]
        for line in self:
            line.language_id = Lang.search([("code", "=", line.language_code), ("active", "=", True)], limit=1)

    def _inverse_language_id(self):
        for line in self:
            line.language_code = line.language_id.code if line.language_id else False

    def _validate_lines(self):
        allowed_ttypes = {key for key, _label in SUPPORTED_CUSTOM_FIELD_TYPES}
        model_names = self.env["ir.model"].search([]).mapped("model")
        for line in self.filtered(lambda record: record.active and record.import_enabled):
            if line.target_model not in FILE_MODEL_WHITELIST.get(line.source_file_key, []):
                raise ValidationError(_("Model %s is not allowed for file type %s.") % (line.target_model, line.source_file_label))
            if line.create_field_if_missing:
                technical_name = (line.new_field_name or line.target_field_name or "").strip()
                if not technical_name.startswith("x_"):
                    raise ValidationError(_("Custom field names must start with x_: %s") % technical_name)
                if line.custom_field_ttype not in allowed_ttypes:
                    raise ValidationError(_("Field type %s is not supported.") % line.custom_field_ttype)
                if line.custom_field_ttype == "many2one" and not line.relation_model:
                    raise ValidationError(_("Many2one field %s requires a relation model.") % technical_name)
                if line.relation_model and line.relation_model not in model_names:
                    raise ValidationError(_("Relation model %s does not exist.") % line.relation_model)

    def _ensure_dynamic_fields(self):
        self._validate_lines()
        for line in self.filtered(lambda l: l.active and l.import_enabled and l.create_field_if_missing and l.target_model):
            if line.target_model == "website":
                continue
            technical_name = (line.new_field_name or line.target_field_name or "").strip()
            model_record = self.env["ir.model"].search([("model", "=", line.target_model)], limit=1)
            if not model_record:
                raise ValidationError(_("Model %s was not found.") % line.target_model)
            existing = self.env["ir.model.fields"].search([("model", "=", line.target_model), ("name", "=", technical_name)], limit=1)
            if existing:
                if existing.ttype != line.custom_field_ttype:
                    raise ValidationError(_("Field %s exists already with type %s instead of %s.") % (technical_name, existing.ttype, line.custom_field_ttype))
                if line.custom_field_ttype == "many2one" and existing.relation != line.relation_model:
                    raise ValidationError(_("Field %s exists already with relation %s instead of %s.") % (technical_name, existing.relation, line.relation_model))
                line.target_field_id = existing
                line.target_field_name = existing.name
                continue
            create_vals = {
                "name": technical_name,
                "field_description": line.new_field_label or line.source_column_label or line.source_column,
                "model_id": model_record.id,
                "model": line.target_model,
                "ttype": line.custom_field_ttype,
                "state": "manual",
            }
            if line.custom_field_ttype == "many2one":
                create_vals["relation"] = line.relation_model
            created = self.env["ir.model.fields"].create(create_vals)
            line.target_field_id = created
            line.target_field_name = created.name
