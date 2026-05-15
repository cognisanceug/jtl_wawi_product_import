from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    _barcode_unique = models.Constraint(
        "unique(barcode)",
        "The barcode must be unique.",
    )

    manufacturer_partner_id = fields.Many2one(
        "res.partner",
        string="Manufacturer",
        domain=[("is_company", "=", True)],
        copy=False,
        index=True,
    )
    manufacturer_id = fields.Many2one(
        "res.partner",
        string="Hersteller",
        related="manufacturer_partner_id",
        store=True,
        readonly=False,
        copy=False,
        index=True,
    )
    eu_responsible_partner_id = fields.Many2one(
        "res.partner",
        string="EU Responsible",
        domain=[("is_company", "=", True)],
        copy=False,
        index=True,
    )
    gdpr_responsible_id = fields.Many2one(
        "res.partner",
        string="EU Responsible",
        related="eu_responsible_partner_id",
        store=True,
        readonly=False,
        copy=False,
        index=True,
    )
    brand_id = fields.Many2one(
        "product.brand",
        string="Marke",
        domain="[('manufacturer_id', '=', manufacturer_id)]",
    )
    manufacturer_sku = fields.Char(string="HAN", copy=False, help="Hersteller-Artikelnummer (Manufacturer Article Number).")
    parent_sku = fields.Char(string="Parent SKU", copy=False, index=True)
    jtl_image_sync_done = fields.Boolean(string="Shop image sync done", default=False, copy=False, index=True)
    length = fields.Float(string="Length", digits=(16, 6), copy=False)
    width = fields.Float(string="Width", digits=(16, 6), copy=False)
    height = fields.Float(string="Height", digits=(16, 6), copy=False)
    volume = fields.Float(string="Volume", digits=(16, 6), copy=False)

    @api.onchange("length", "width", "height")
    def _onchange_dimensions_sync_volume(self):
        self._sync_volume_from_dimensions()

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        templates._sync_volume_from_dimensions()
        templates._sync_fields_from_brand()
        return templates

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("skip_volume_sync") and {"length", "width", "height"} & set(vals):
            self._sync_volume_from_dimensions()
        if "brand_id" in vals:
            self._sync_fields_from_brand()
        return res

    def _sync_volume_from_dimensions(self):
        if "volume" not in self._fields:
            return
        for template in self:
            volume = (template.length or 0.0) * (template.width or 0.0) * (template.height or 0.0)
            if template.volume != volume:
                if template.id:
                    template.with_context(skip_volume_sync=True).write({"volume": volume})
                else:
                    template.volume = volume

    @api.onchange("manufacturer_id")
    def _onchange_manufacturer_id_reset_brand(self):
        for template in self:
            if template.brand_id and template.brand_id.manufacturer_id != template.manufacturer_id:
                template.brand_id = False

    @api.onchange("brand_id")
    def _onchange_brand_id_set_manufacturer(self):
        for template in self:
            if template.brand_id:
                template.manufacturer_id = template.brand_id.manufacturer_id.id or False
                template.eu_responsible_partner_id = template.brand_id.gdpr_responsible_id.id or False

    def _sync_fields_from_brand(self):
        for template in self:
            if not template.brand_id:
                continue
            template.write(
                {
                    "manufacturer_id": template.brand_id.manufacturer_id.id or False,
                    "eu_responsible_partner_id": template.brand_id.gdpr_responsible_id.id or False,
                }
            )

    def action_open_brand_assign_wizard(self):
        if not self:
            raise UserError(_("Bitte waehlen Sie mindestens ein Produkt aus."))
        view = self.env.ref("jtl_wawi_product_import.view_product_brand_assign_wizard_form")
        return {
            "name": "Marke zuweisen",
            "type": "ir.actions.act_window",
            "res_model": "product.brand.assign.wizard",
            "view_mode": "form",
            "view_id": view.id,
            "target": "new",
            "context": {
                "active_model": "product.template",
                "active_ids": self.ids,
            },
        }


class ProductProduct(models.Model):
    _inherit = "product.product"

    brand_id = fields.Many2one(
        "product.brand",
        string="Marke",
        related="product_tmpl_id.brand_id",
        store=True,
        readonly=False,
    )
    parent_sku = fields.Char(string="Parent SKU", copy=False, index=True)

    def action_open_brand_assign_wizard(self):
        templates = self.mapped("product_tmpl_id")
        if not templates:
            raise UserError(_("Bitte waehlen Sie mindestens ein Produkt aus."))
        return templates.action_open_brand_assign_wizard()
