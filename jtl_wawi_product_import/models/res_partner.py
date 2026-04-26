import re

from odoo import api, fields, models


def _normalize_name(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


class ResPartnerCategory(models.Model):
    _inherit = "res.partner.category"

    is_manufacturer_tag = fields.Boolean(string="Manufacturer Tag")


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_manufacturer = fields.Boolean(
        string="Manufacturer",
        index=True,
        copy=False,
        help="Contact is marked as a manufacturer.",
    )
    is_gdpr_responsible = fields.Boolean(
        string="EU Responsible",
        index=True,
        copy=False,
        help="Legacy compatibility flag. Use EU Responsible in the UI and import.",
    )
    manufacturer_external_id = fields.Char(string="Manufacturer External ID", index=True, copy=False)
    is_eu_responsible = fields.Boolean(string="EU Responsible", index=True, copy=False)
    eu_responsible_external_id = fields.Char(string="EU Responsible External ID", index=True, copy=False)
    brand_ids = fields.One2many("product.brand", "manufacturer_id", string="Brands")
    normalized_name = fields.Char(
        string="Normalized Name",
        compute="_compute_normalized_name",
        store=True,
        index=True,
        copy=False,
    )

    @api.depends("name")
    def _compute_normalized_name(self):
        for partner in self:
            partner.normalized_name = _normalize_name(partner.name)
