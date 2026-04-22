from odoo import api, fields, models
from odoo.exceptions import ValidationError


class JtlImportMapping(models.Model):
    _name = "jtl.import.mapping"
    _description = "JTL Import Mapping"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    source_column = fields.Char(required=True, index=True)
    target_model = fields.Selection(
        [
            ("product.template", "Product Template"),
            ("product.product", "Product Variant"),
            ("res.partner", "Partner"),
            ("product.category", "Product Category"),
            ("product.supplierinfo", "Supplierinfo"),
            ("product.attribute", "Product Attribute"),
            ("product.image", "Product Image"),
            ("account.tax", "Tax"),
            ("stock.quant", "Stock"),
            ("website", "Website"),
        ],
        required=True,
    )
    target_field = fields.Char(required=True, index=True)
    language_code = fields.Char()
    transform_logic = fields.Selection(
        [
            ("text", "Text"),
            ("decimal", "Decimal"),
            ("boolean", "Boolean"),
            ("html", "HTML"),
            ("integer", "Integer"),
            ("date", "Date"),
            ("path", "Path"),
        ],
        default="text",
        required=True,
    )
    required = fields.Boolean(default=False)
    active = fields.Boolean(default=True)
    default_value = fields.Char()

    _sql_constraints = [
        (
            "jtl_import_mapping_unique",
            "unique(source_column, target_model, target_field, language_code)",
            "The mapping combination must be unique.",
        )
    ]

    @api.constrains("source_column", "target_field")
    def _check_trimmed_values(self):
        for record in self:
            if record.source_column != (record.source_column or "").strip():
                raise ValidationError("Source column cannot start or end with spaces.")
            if record.target_field != (record.target_field or "").strip():
                raise ValidationError("Target field cannot start or end with spaces.")
