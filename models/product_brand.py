from odoo import fields, models


class ProductBrand(models.Model):
    _name = "product.brand"
    _description = "Product Brand"
    _order = "name"

    name = fields.Char(string="Marke", required=True, index=True)
    manufacturer_id = fields.Many2one(
        "res.partner",
        string="Hersteller",
        domain=["|", ("supplier_rank", ">", 0), ("is_manufacturer", "=", True)],
    )
    gdpr_responsible_id = fields.Many2one(
        "res.partner",
        string="EU Responsible",
        domain=[("is_eu_responsible", "=", True)],
        help="EU responsible person for this brand.",
    )
    active = fields.Boolean(default=True)
    map_ids = fields.One2many("brand.channel.map", "brand_id", string="Brand Mappings")
    brand_external_id = fields.Char(string="Brand External ID", index=True, copy=False)

    def write(self, vals):
        res = super().write(vals)
        if "manufacturer_id" in vals or "gdpr_responsible_id" in vals:
            templates = self.env["product.template"].search([("brand_id", "in", self.ids)])
            templates._sync_fields_from_brand()
        return res


class BrandChannelMap(models.Model):
    _name = "brand.channel.map"
    _description = "Brand Channel Mapping"
    _order = "channel, external_name"

    brand_id = fields.Many2one("product.brand", string="Marke", required=True, ondelete="cascade")
    channel = fields.Selection(
        [
            ("shop_apotheke", "Shop-Apotheke"),
            ("mirakl", "Mirakl"),
        ],
        string="Kanal",
        required=True,
        default="shop_apotheke",
    )
    external_name = fields.Char(string="Externer Markenname", required=True)
    external_id = fields.Char(string="Externe Marken-ID")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "brand_channel_unique",
            "unique(brand_id, channel)",
            "Es existiert bereits ein Mapping fuer diese Marke und diesen Kanal.",
        )
    ]
