import re

from odoo import api, fields, models


def _normalize_name(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


class ResPartnerCategory(models.Model):
    _inherit = "res.partner.category"

    is_jtl_manufacturer_tag = fields.Boolean(string="JTL Manufacturer Tag")


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_jtl_manufacturer = fields.Boolean(string="JTL Manufacturer", index=True, copy=False)
    jtl_manufacturer_external_id = fields.Char(string="JTL Manufacturer External ID", index=True, copy=False)
    jtl_normalized_name = fields.Char(
        string="JTL Normalized Name",
        compute="_compute_jtl_normalized_name",
        store=True,
        index=True,
        copy=False,
    )

    @api.depends("name")
    def _compute_jtl_normalized_name(self):
        for partner in self:
            partner.jtl_normalized_name = _normalize_name(partner.name)

