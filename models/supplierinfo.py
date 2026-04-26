from odoo import fields, models


class ProductSupplierinfo(models.Model):
    _inherit = "product.supplierinfo"

    is_default_supplier = fields.Boolean(string="Default Supplier", copy=False)
