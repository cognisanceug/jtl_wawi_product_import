from odoo import api, fields, models
from odoo.exceptions import ValidationError

SOURCE_FILE_SELECTION = [
    ("article_master", "Artikelstammdaten"),
    ("manufacturer", "Hersteller"),
    ("eu_representative", "EU RP"),
    ("category", "Kategorien"),
    ("supplierinfo", "Lieferantenartikel"),
    ("variation_combination", "Variationskombinationen"),
    ("variation_definition", "Variationen"),
    ("attribute", "Attribute"),
    ("feature", "Eigene Merkmale"),
    ("bom", "Stueckliste"),
]


class JtlImportMapping(models.Model):
    _name = "jtl.import.mapping"
    _description = "JTL Import Mapping"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    source_file_key = fields.Selection(SOURCE_FILE_SELECTION, required=True, default="article_master", index=True)
    source_column = fields.Char(required=True, index=True)
    target_model = fields.Selection(
        [
            ("product.template", "Product Template"),
            ("product.product", "Product Variant"),
            ("res.partner", "Partner"),
            ("product.category", "Product Category"),
            ("product.supplierinfo", "Supplierinfo"),
            ("product.attribute", "Product Attribute"),
            ("product.image", "Product Image"),
            ("account.tax", "Tax"),
            ("stock.quant", "Stock"),
            ("website", "Website"),
        ],
        required=True,
    )
    target_field = fields.Char(required=True, index=True)
    language_code = fields.Char()
    language_id = fields.Many2one(
        "res.lang",
        string="Sprache",
        compute="_compute_language_id",
        inverse="_inverse_language_id",
        readonly=False,
        domain=[("active", "=", True)],
    )
    transform_logic = fields.Selection(
        [
            ("trim", "Trim"),
            ("uppercase", "Grossschreibung"),
            ("lowercase", "Kleinschreibung"),
            ("decimal_comma", "Dezimal-Komma zu Punkt"),
            ("boolean_normalize", "Boolean normalisieren"),
            ("html_clean", "HTML bereinigen"),
            ("integer", "Integer"),
            ("slug", "Slug"),
            ("path", "Path"),
            ("text", "Keine"),
        ],
        default="trim",
        required=True,
    )
    required = fields.Boolean(default=False)
    active = fields.Boolean(default=True)
    default_value = fields.Char()

    _sql_constraints = [
        (
            "jtl_import_mapping_unique",
            "unique(source_file_key, source_column, target_model, target_field, language_code)",
            "The mapping combination must be unique.",
        )
    ]

    @api.constrains("source_column", "target_field")
    def _check_trimmed_values(self):
        for record in self:
            if record.source_column != (record.source_column or "").strip():
                raise ValidationError("Source column cannot start or end with spaces.")
            if record.target_field != (record.target_field or "").strip():
                raise ValidationError("Target field cannot start or end with spaces.")

    @api.depends("language_code")
    def _compute_language_id(self):
        Lang = self.env["res.lang"]
        for record in self:
            record.language_id = Lang.search([("code", "=", record.language_code), ("active", "=", True)], limit=1)

    def _inverse_language_id(self):
        for record in self:
            record.language_code = record.language_id.code if record.language_id else False
