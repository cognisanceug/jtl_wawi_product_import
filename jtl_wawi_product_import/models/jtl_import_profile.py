from odoo import api, fields, models

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


class JtlImportProfile(models.Model):
    _name = "jtl.import.profile"
    _description = "JTL Import Profile"
    _order = "name, id"

    name = fields.Char(required=True, translate=True)
    description = fields.Text()
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(default=False)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    line_ids = fields.One2many("jtl.import.profile.line", "profile_id", string="Mappings", copy=True)

    def action_duplicate_profile(self):
        self.ensure_one()
        duplicate = self.copy(default={"name": "%s (Copy)" % self.name, "is_default": False})
        return {
            "type": "ir.actions.act_window",
            "res_model": "jtl.import.profile",
            "res_id": duplicate.id,
            "view_mode": "form",
            "target": "current",
        }


class JtlImportProfileLine(models.Model):
    _name = "jtl.import.profile.line"
    _description = "JTL Import Profile Line"
    _order = "sequence, id"

    profile_id = fields.Many2one("jtl.import.profile", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    source_file_key = fields.Selection(SOURCE_FILE_SELECTION, required=True, default="article_master")
    source_column = fields.Char(required=True)
    source_column_label = fields.Char()
    sample_value = fields.Char()
    target_model = fields.Char(required=True)
    target_field = fields.Char(required=True)
    create_field = fields.Boolean(default=False)
    new_field_name = fields.Char()
    new_field_label = fields.Char()
    new_field_type = fields.Selection(
        [
            ("char", "Char"),
            ("text", "Text"),
            ("html", "HTML"),
            ("integer", "Integer"),
            ("float", "Float"),
            ("boolean", "Boolean"),
            ("many2one", "Many2one"),
        ],
        default="char",
    )
    relation_model = fields.Char()
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
            ("decimal", "Dezimal"),
            ("decimal_comma", "Dezimal-Komma zu Punkt"),
            ("boolean", "Boolean"),
            ("boolean_normalize", "Boolean normalisieren"),
            ("html", "HTML"),
            ("html_clean", "HTML bereinigen"),
            ("integer", "Integer"),
            ("slug", "Slug"),
            ("path", "Path"),
            ("char", "Zeichenkette"),
            ("selection", "Selection"),
            ("many2one", "Many2one"),
            ("ignore_empty", "Leere ignorieren"),
            ("text", "Keine"),
        ],
        default="trim",
        required=True,
    )
    required = fields.Boolean(default=False)
    active = fields.Boolean(default=True)
    default_value = fields.Char()
    import_enabled = fields.Boolean(default=True)

    @api.depends("language_code")
    def _compute_language_id(self):
        Lang = self.env["res.lang"]
        for record in self:
            record.language_id = Lang.search([("code", "=", record.language_code), ("active", "=", True)], limit=1)

    def _inverse_language_id(self):
        for record in self:
            record.language_code = record.language_id.code if record.language_id else False
