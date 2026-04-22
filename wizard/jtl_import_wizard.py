import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


TARGET_MODEL_SELECTION = [
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
]

CURATED_MODEL_FIELDS = {
    "product.template": [
        "name",
        "barcode",
        "weight",
        "active",
        "list_price",
        "standard_price",
        "categ_id",
        "description_sale",
        "description",
        "manufacturer_partner_id",
        "manufacturer_sku",
        "jtl_parent_sku",
        "jtl_short_description",
        "jtl_description_html",
        "jtl_seo_path",
        "jtl_taric_code",
        "jtl_meta_title",
        "jtl_meta_description",
        "country_of_origin",
    ],
    "product.product": [
        "default_code",
        "barcode",
        "weight",
        "active",
        "jtl_parent_sku",
    ],
    "res.partner": [
        "name",
        "email",
        "website",
        "phone",
        "mobile",
        "street",
        "street2",
        "zip",
        "city",
        "country_id",
        "is_company",
        "is_jtl_manufacturer",
        "jtl_manufacturer_external_id",
    ],
    "product.category": ["name", "parent_id"],
    "product.supplierinfo": ["partner_id", "product_tmpl_id", "product_id", "product_code", "price", "delay", "min_qty", "jtl_is_default_supplier"],
    "product.attribute": ["name", "create_variant"],
    "account.tax": ["amount", "name"],
    "stock.quant": ["inventory_quantity", "location_id"],
}


class JtlImportWizard(models.TransientModel):
    _name = "jtl.import.wizard"
    _description = "JTL Import Wizard"

    file_name = fields.Char(required=True)
    file_data = fields.Binary(required=True)
    batch_size = fields.Integer(default=200, required=True)
    dry_run = fields.Boolean(default=False)
    import_stock = fields.Boolean(default=False)
    import_images = fields.Boolean(default=True)
    import_gallery_images = fields.Boolean(default=False)
    import_seo = fields.Boolean(default=False)
    profile_id = fields.Many2one("jtl.import.profile", string="Mapping Profile", domain="[('active', '=', True)]")
    save_profile = fields.Boolean(string="Save as Profile")
    profile_name = fields.Char(string="New Profile Name")
    mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Field Mapping")
    headers_loaded = fields.Boolean(readonly=True)

    def _get_default_mapping_lookup(self, headers):
        if self.profile_id:
            return {line.source_column: line for line in self.profile_id.line_ids.filtered(lambda l: l.active and l.source_column in headers)}
        default_mappings = self.env["jtl.import.mapping"].search([("active", "=", True), ("source_column", "in", headers)], order="sequence, id")
        mapping_by_column = {}
        for mapping in default_mappings:
            mapping_by_column.setdefault(mapping.source_column, mapping)
        return mapping_by_column

    def _build_mapping_lines(self, headers):
        mapping_by_column = self._get_default_mapping_lookup(headers)

        line_commands = [(5, 0, 0)]
        for sequence, header in enumerate(headers, start=1):
            mapping = mapping_by_column.get(header)
            target_field_id = False
            target_field_name = mapping.target_field if mapping else False
            if mapping and mapping.target_model != "website":
                target_field = self.env["ir.model.fields"].search(
                    [("model", "=", mapping.target_model), ("name", "=", mapping.target_field)],
                    limit=1,
                )
                target_field_id = target_field.id if target_field else False
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "sequence": sequence * 10,
                        "source_column": header,
                        "target_model": mapping.target_model if mapping else False,
                        "target_field_id": target_field_id,
                        "target_field_name": target_field_name,
                        "language_code": mapping.language_code if mapping else False,
                        "transform_logic": mapping.transform_logic if mapping else "text",
                        "required": mapping.required if mapping else False,
                        "active": bool(mapping),
                        "default_value": mapping.default_value if mapping else False,
                    },
                )
            )
        self.mapping_line_ids = line_commands
        self.headers_loaded = True

    def action_load_columns(self):
        self.ensure_one()
        if not self.file_data:
            raise UserError(_("Please upload a CSV file first."))
        headers = self.env["jtl.import.parser"].extract_headers(self.file_data)
        self._build_mapping_lines(headers)
        return {
            "type": "ir.actions.act_window",
            "name": _("New JTL Import"),
            "res_model": "jtl.import.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_apply_profile(self):
        self.ensure_one()
        if not self.file_data:
            raise UserError(_("Please upload a CSV file first."))
        if not self.profile_id:
            raise UserError(_("Please select a mapping profile first."))
        headers = self.env["jtl.import.parser"].extract_headers(self.file_data)
        self._build_mapping_lines(headers)
        return {
            "type": "ir.actions.act_window",
            "name": _("New JTL Import"),
            "res_model": "jtl.import.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _save_profile_from_lines(self):
        self.ensure_one()
        if not self.save_profile:
            return False
        profile_name = (self.profile_name or "").strip()
        if not profile_name:
            raise UserError(_("Please enter a profile name if you want to save the mapping profile."))
        profile = self.env["jtl.import.profile"].create(
            {
                "name": profile_name,
                "company_id": self.env.company.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": line.sequence,
                            "source_column": line.source_column,
                            "target_model": line.target_model,
                            "target_field": line.target_field_id.name or line.target_field_name,
                            "language_code": line.language_code,
                            "transform_logic": line.transform_logic,
                            "required": line.required,
                            "active": line.active,
                            "default_value": line.default_value,
                        },
                    )
                    for line in self.mapping_line_ids
                    if line.target_model and (line.target_field_id or line.target_field_name)
                ],
            }
        )
        self.profile_id = profile
        return profile

    def _get_selected_mapping_specs(self):
        self.ensure_one()
        self.mapping_line_ids._ensure_dynamic_fields()
        specs = []
        for line in self.mapping_line_ids.filtered("active"):
            target_field = line.target_field_id.name or line.target_field_name
            if not line.target_model or not target_field:
                raise UserError(_("Please complete the mapping for column '%s'.") % line.source_column)
            specs.append(
                {
                    "sequence": line.sequence,
                    "source_column": line.source_column,
                    "target_model": line.target_model,
                    "target_field": target_field,
                    "language_code": line.language_code or False,
                    "transform_logic": line.transform_logic,
                    "required": line.required,
                    "default_value": line.default_value or False,
                }
            )
        if not specs:
            raise UserError(_("Please activate and map at least one CSV column."))
        return specs

    def action_validate(self):
        self.ensure_one()
        if not self.file_data:
            raise UserError(_("Please upload a CSV file first."))
        if not self.mapping_line_ids:
            self.action_load_columns()
        mapping_specs = self._get_selected_mapping_specs()
        self._save_profile_from_lines()
        run = self.env["jtl.import.run"].create(
            {
                "filename": self.file_name,
                "batch_size": self.batch_size,
                "dry_run": self.dry_run,
                "import_stock": self.import_stock,
                "import_images": self.import_images,
                "import_gallery_images": self.import_gallery_images,
                "import_seo": self.import_seo,
            }
        )
        run.set_source_file(self.file_name, self.file_data)
        payload = self.env["jtl.import.parser"].parse_import_file(run, self.file_data, self.file_name, mapping_specs=mapping_specs)
        run.set_payload(payload)
        run.write(
            {
                "state": "validated",
                "validation_message": _("%s product groups are staged and ready for queueing.") % len(payload.get("skus", [])),
            }
        )
        if payload.get("warnings"):
            run._append_logs(payload["warnings"])
        return {
            "type": "ir.actions.act_window",
            "name": _("JTL Import Run"),
            "res_model": "jtl.import.run",
            "res_id": run.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_validate_and_queue(self):
        action = self.action_validate()
        run = self.env["jtl.import.run"].browse(action["res_id"])
        run.action_queue()
        return action


class JtlImportWizardLine(models.TransientModel):
    _name = "jtl.import.wizard.line"
    _description = "JTL Import Wizard Line"
    _order = "sequence, id"

    wizard_id = fields.Many2one("jtl.import.wizard", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    source_column = fields.Char(required=True, readonly=True)
    target_model = fields.Selection(TARGET_MODEL_SELECTION)
    available_field_ids = fields.Many2many("ir.model.fields", compute="_compute_available_field_ids")
    target_field_id = fields.Many2one("ir.model.fields", string="Odoo Field", domain="[('id', 'in', available_field_ids)]")
    target_field_name = fields.Char(string="Target Field")
    create_field_if_missing = fields.Boolean(string="Create Field")
    custom_field_ttype = fields.Selection(
        [
            ("char", "Text"),
            ("text", "Long Text"),
            ("html", "HTML"),
            ("float", "Decimal"),
            ("integer", "Integer"),
            ("boolean", "Boolean"),
            ("date", "Date"),
        ],
        default="char",
    )
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
    active = fields.Boolean(default=False)
    default_value = fields.Char()

    @api.onchange("target_field_id")
    def _onchange_target_field_id(self):
        for line in self:
            if line.target_field_id:
                line.target_field_name = line.target_field_id.name

    @api.depends("target_model")
    def _compute_available_field_ids(self):
        ir_model_fields = self.env["ir.model.fields"]
        for line in self:
            if not line.target_model or line.target_model == "website":
                line.available_field_ids = [(6, 0, [])]
                continue
            allowed_names = set(CURATED_MODEL_FIELDS.get(line.target_model, []))
            base_domain = [("model", "=", line.target_model)]
            curated = ir_model_fields.search(base_domain + [("name", "in", list(allowed_names))]) if allowed_names else ir_model_fields.browse()
            custom = ir_model_fields.search(base_domain + [("name", "=like", "x_%")])
            line.available_field_ids = [(6, 0, (curated | custom).ids)]

    @api.onchange("source_column", "create_field_if_missing")
    def _onchange_source_column_generate_field(self):
        for line in self:
            if line.create_field_if_missing and not line.target_field_id and not line.target_field_name and line.source_column:
                sanitized = re.sub(r"[^a-z0-9_]+", "_", line.source_column.strip().lower())
                sanitized = re.sub(r"_+", "_", sanitized).strip("_")
                if sanitized and not sanitized.startswith("x_"):
                    sanitized = "x_%s" % sanitized
                line.target_field_name = sanitized

    def _ensure_dynamic_fields(self):
        for line in self.filtered(lambda l: l.active and l.create_field_if_missing and l.target_model and l.target_field_name and not l.target_field_id):
            if line.target_model == "website":
                continue
            model_record = self.env["ir.model"].search([("model", "=", line.target_model)], limit=1)
            if not model_record:
                continue
            technical_name = line.target_field_name.strip()
            if not technical_name.startswith("x_"):
                technical_name = "x_%s" % technical_name
            existing = self.env["ir.model.fields"].search(
                [("model", "=", line.target_model), ("name", "=", technical_name)],
                limit=1,
            )
            if not existing:
                existing = self.env["ir.model.fields"].create(
                    {
                        "name": technical_name,
                        "field_description": line.source_column,
                        "model_id": model_record.id,
                        "model": line.target_model,
                        "ttype": line.custom_field_ttype,
                        "state": "manual",
                    }
                )
            line.target_field_id = existing
            line.target_field_name = existing.name
