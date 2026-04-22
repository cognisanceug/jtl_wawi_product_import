from odoo import fields, models


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
    source_column = fields.Char(required=True)
    target_model = fields.Char(required=True)
    target_field = fields.Char(required=True)
    language_code = fields.Char()
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
