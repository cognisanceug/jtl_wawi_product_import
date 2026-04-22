from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    manufacturer_partner_id = fields.Many2one(
        "res.partner",
        string="Manufacturer",
        domain=[("is_company", "=", True)],
        copy=False,
        index=True,
    )
    manufacturer_sku = fields.Char(string="Manufacturer SKU", copy=False)
    jtl_parent_sku = fields.Char(string="JTL Parent SKU", copy=False, index=True)
    jtl_short_description = fields.Html(string="JTL Short Description", translate=True)
    jtl_description_html = fields.Html(string="JTL Description", translate=True)
    jtl_seo_path = fields.Char(string="JTL SEO Path", copy=False)
    jtl_taric_code = fields.Char(string="JTL TARIC Code", copy=False)
    jtl_meta_title = fields.Char(string="JTL Meta Title", translate=True, copy=False)
    jtl_meta_description = fields.Text(string="JTL Meta Description", translate=True, copy=False)


class ProductProduct(models.Model):
    _inherit = "product.product"

    jtl_parent_sku = fields.Char(string="JTL Parent SKU", copy=False, index=True)
