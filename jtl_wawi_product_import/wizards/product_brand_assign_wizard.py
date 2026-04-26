from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProductBrandAssignWizard(models.TransientModel):
    _name = "product.brand.assign.wizard"
    _description = "Bulk Brand Assignment"

    brand_id = fields.Many2one("product.brand", string="Marke", required=True)
    product_count = fields.Integer(string="Anzahl Produkte", readonly=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        templates = self._get_target_templates()
        values["product_count"] = len(templates)
        return values

    @api.model
    def _get_target_templates(self):
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids") or []
        if active_model == "product.template":
            return self.env["product.template"].browse(active_ids).exists()
        if active_model == "product.product":
            variants = self.env["product.product"].browse(active_ids).exists()
            return variants.mapped("product_tmpl_id")
        return self.env["product.template"]

    def action_apply(self):
        self.ensure_one()
        templates = self._get_target_templates()
        if not templates:
            raise UserError(_("Es wurden keine Produkte zur Verarbeitung ausgewaehlt."))
        templates.write({"brand_id": self.brand_id.id})
        return {"type": "ir.actions.act_window_close"}
