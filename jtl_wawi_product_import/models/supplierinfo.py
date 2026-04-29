from odoo import fields, models


class ProductSupplierinfo(models.Model):
    _inherit = "product.supplierinfo"

    is_default_supplier = fields.Boolean(string="Default Supplier", copy=False)
    manufacturer_sku = fields.Char(
        string="HAN",
        copy=False,
        help="Hersteller-Artikelnummer (Manufacturer Article Number) used by the manufacturer for this product / supplier combination.",
    )
