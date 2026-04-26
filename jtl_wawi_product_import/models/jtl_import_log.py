from odoo import fields, models


class JtlImportLog(models.Model):
    _name = "jtl.import.log"
    _description = "JTL Import Log"
    _order = "id desc"

    run_id = fields.Many2one("jtl.import.run", required=True, ondelete="cascade", index=True)
    batch_number = fields.Integer(index=True)
    row_number = fields.Integer(index=True)
    article_number = fields.Char(string="Artikelnummer", index=True)
    field_name = fields.Char()
    level = fields.Selection(
        [("info", "Info"), ("warning", "Warning"), ("error", "Error")],
        required=True,
        default="info",
        index=True,
    )
    message = fields.Text(required=True)

