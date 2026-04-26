from odoo import api, fields, models

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


class JtlImportProfile(models.Model):
    _name = "jtl.import.profile"
    _description = "JTL Import Profile"
    _order = "name, id"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    line_ids = fields.One2many("jtl.import.profile.line", "profile_id", string="Mappings", copy=True)


class JtlImportProfileLine(models.Model):
    _name = "jtl.import.profile.line"
    _description = "JTL Import Profile Line"
    _order = "sequence, id"

    profile_id = fields.Many2one("jtl.import.profile", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    source_file_key = fields.Selection(SOURCE_FILE_SELECTION, required=True, default="article_master")
    source_column = fields.Char(required=True)
    target_model = fields.Char(required=True)
    target_field = fields.Char(required=True)
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
            ("text", "Text"),
            ("decimal", "Decimal"),
            ("boolean", "Boolean"),
            ("html", "HTML"),
            ("integer", "Integer"),
            ("date", "Date"),
            ("path", "Path"),
        ],
        default="text",
        required=True,
    )
    required = fields.Boolean(default=False)
    active = fields.Boolean(default=True)
    default_value = fields.Char()

    @api.depends("language_code")
    def _compute_language_id(self):
        Lang = self.env["res.lang"]
        for record in self:
            record.language_id = Lang.search([("code", "=", record.language_code), ("active", "=", True)], limit=1)

    def _inverse_language_id(self):
        for record in self:
            record.language_code = record.language_id.code if record.language_id else False
