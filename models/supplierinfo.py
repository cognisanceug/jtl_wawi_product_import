from odoo import fields, models


class ProductSupplierinfo(models.Model):
    _inherit = "product.supplierinfo"

    jtl_is_default_supplier = fields.Boolean(string="JTL Default Supplier", copy=False)

