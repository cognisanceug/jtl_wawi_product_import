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

SOURCE_FILE_SELECTION = [
    ("article_master", "Artikelstammdaten"),
    ("manufacturer", "Hersteller"),
    ("eu_representative", "EU RP"),
    ("category", "Kategorien"),
    ("supplierinfo", "Lieferantenartikel"),
    ("variation_combination", "Variationskombinationen"),
    ("variation_definition", "Variationen"),
    ("attribute", "Attribute"),
    ("feature", "Eigene Merkmale"),
    ("bom", "Stueckliste"),
]

SOURCE_FILE_LABELS = dict(SOURCE_FILE_SELECTION)

def normalize_header_key(header):
    key = (header or "").strip().lower()
    key = key.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    key = re.sub(r"\([^)]*\)", "", key)
    key = re.sub(r"[^a-z0-9]+", "", key)
    return key

AUTO_MAPPING_ALIASES = {
    "article_master": {
        "artikelnummer": ("product.product", "default_code", "text"),
        "sku": ("product.product", "default_code", "text"),
        "ean": ("product.template", "barcode", "text"),
        "gtin": ("product.template", "barcode", "text"),
        "han": ("product.template", "manufacturer_sku", "text"),
        "name": ("product.template", "name", "text"),
        "artikelname": ("product.template", "name", "text"),
        "gewicht": ("product.template", "weight", "decimal"),
        "artikelgewicht": ("product.template", "weight", "decimal"),
        "breite": ("product.template", "width", "decimal"),
        "weite": ("product.template", "width", "decimal"),
        "laenge": ("product.template", "length", "decimal"),
        "lange": ("product.template", "length", "decimal"),
        "tiefe": ("product.template", "length", "decimal"),
        "hoehe": ("product.template", "height", "decimal"),
        "hohe": ("product.template", "height", "decimal"),
        "volumen": ("product.template", "volume", "decimal"),
        "bruttopreis": ("product.template", "gross_sales_price", "decimal"),
        "verkaufspreis": ("product.template", "sale_price", "decimal"),
        "nettoek": ("product.template", "purchase_price", "decimal"),
        "netto_ek": ("product.template", "purchase_price", "decimal"),
        "steuersatz": ("account.tax", "tax_rate", "decimal"),
        "kategorie": ("product.category", "category_path", "text"),
        "hersteller": ("res.partner", "manufacturer_name", "text"),
        "herstelleremail": ("res.partner", "manufacturer_email", "text"),
        "herstellerwebsite": ("res.partner", "manufacturer_website", "text"),
        "herstellerid": ("res.partner", "manufacturer_external_id", "text"),
        "marke": ("product.template", "brand_name", "text"),
        "brand": ("product.template", "brand_name", "text"),
        "eurp": ("res.partner", "eu_responsible_name", "text"),
        "eurepresentative": ("res.partner", "eu_responsible_name", "text"),
        "eurepresentativeemail": ("res.partner", "eu_responsible_email", "text"),
        "eurepresentativewebsite": ("res.partner", "eu_responsible_website", "text"),
        "eurpid": ("res.partner", "eu_responsible_external_id", "text"),
        "eurepresentativeid": ("res.partner", "eu_responsible_external_id", "text"),
    },
    "manufacturer": {
        "name": ("res.partner", "name", "text"),
        "hersteller": ("res.partner", "name", "text"),
        "email": ("res.partner", "email", "text"),
        "website": ("res.partner", "website", "text"),
        "id": ("res.partner", "manufacturer_external_id", "text"),
        "herstellerid": ("res.partner", "manufacturer_external_id", "text"),
    },
    "eu_representative": {
        "name": ("res.partner", "name", "text"),
        "eurp": ("res.partner", "name", "text"),
        "eurepresentative": ("res.partner", "name", "text"),
        "email": ("res.partner", "email", "text"),
        "website": ("res.partner", "website", "text"),
        "id": ("res.partner", "eu_responsible_external_id", "text"),
        "eurpid": ("res.partner", "eu_responsible_external_id", "text"),
    },
}

CURATED_MODEL_FIELDS = {
    "product.template": [
        "name",
        "barcode",
        "weight",
        "length",
        "width",
        "height",
        "volume",
        "active",
        "list_price",
        "standard_price",
        "categ_id",
        "description_sale",
        "description",
        "website_description",
        "manufacturer_id",
        "eu_responsible_partner_id",
        "brand_id",
        "manufacturer_partner_id",
        "manufacturer_sku",
        "parent_sku",
        "seo_path",
        "hs_code",
        "meta_title",
        "meta_description",
        "country_of_origin",
    ],
    "product.product": [
        "default_code",
        "barcode",
        "weight",
        "active",
        "parent_sku",
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
        "is_manufacturer",
        "manufacturer_external_id",
        "is_eu_responsible",
        "eu_responsible_external_id",
    ],
    "product.category": ["name", "parent_id"],
    "product.supplierinfo": ["partner_id", "product_tmpl_id", "product_id", "product_code", "price", "delay", "min_qty", "is_default_supplier"],
    "product.attribute": ["name", "create_variant"],
    "account.tax": ["amount", "name"],
    "stock.quant": ["inventory_quantity", "location_id"],
}


class JtlImportWizard(models.TransientModel):
    _name = "jtl.import.wizard"
    _description = "JTL Import Wizard"
    _rec_name = "name"

    name = fields.Char(default=lambda self: _("New JTL Import"))
    article_master_file_name = fields.Char()
    article_master_file_data = fields.Binary(string="Artikeldaten")
    manufacturer_file_name = fields.Char()
    manufacturer_file_data = fields.Binary(string="Herstellerdaten")
    eu_representative_file_name = fields.Char()
    eu_representative_file_data = fields.Binary(string="EU-RP-Daten")
    category_file_name = fields.Char()
    category_file_data = fields.Binary(string="Kategoriedaten")
    supplierinfo_file_name = fields.Char()
    supplierinfo_file_data = fields.Binary(string="Lieferantenartikel")
    variation_combination_file_name = fields.Char()
    variation_combination_file_data = fields.Binary(string="Variationskombinationen")
    variation_definition_file_name = fields.Char()
    variation_definition_file_data = fields.Binary(string="Variationsdefinitionen")
    attribute_file_name = fields.Char()
    attribute_file_data = fields.Binary(string="Attributdaten")
    feature_file_name = fields.Char()
    feature_file_data = fields.Binary(string="Merkmalsdaten")
    bom_file_name = fields.Char()
    bom_file_data = fields.Binary(string="Stueckliste")
    batch_size = fields.Integer(default=200, required=True)
    dry_run = fields.Boolean(default=False)
    update_existing_only = fields.Boolean(default=False, string="Update Existing Only")
    import_stock = fields.Boolean(default=False)
    import_images = fields.Boolean(default=True)
    import_gallery_images = fields.Boolean(default=False)
    import_seo = fields.Boolean(default=False)
    barcode_match_update = fields.Boolean(default=False, string="Barcode Match Update")
    contacts_installed = fields.Boolean(compute="_compute_optional_module_status")
    website_sale_installed = fields.Boolean(compute="_compute_optional_module_status")
    product_variants_enabled = fields.Boolean(compute="_compute_optional_module_status")
    precheck_message = fields.Html(compute="_compute_precheck_message", sanitize=False)
    profile_id = fields.Many2one("jtl.import.profile", string="Mapping Profile", domain="[('active', '=', True)]")
    save_profile = fields.Boolean(string="Save as Profile")
    profile_name = fields.Char(string="New Profile Name")
    mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Field Mapping")
    article_master_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Artikelstammdaten", domain=[("source_file_key", "=", "article_master")])
    manufacturer_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Herstellerdaten", domain=[("source_file_key", "=", "manufacturer")])
    eu_representative_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="EU-RP-Daten", domain=[("source_file_key", "=", "eu_representative")])
    category_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Kategoriedaten", domain=[("source_file_key", "=", "category")])
    supplierinfo_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Lieferantenartikel", domain=[("source_file_key", "=", "supplierinfo")])
    variation_combination_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Variationskombinationen", domain=[("source_file_key", "=", "variation_combination")])
    variation_definition_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Variationen", domain=[("source_file_key", "=", "variation_definition")])
    attribute_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Attribute", domain=[("source_file_key", "=", "attribute")])
    feature_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Merkmalsdaten", domain=[("source_file_key", "=", "feature")])
    bom_mapping_line_ids = fields.One2many("jtl.import.wizard.line", "wizard_id", string="Stueckliste", domain=[("source_file_key", "=", "bom")])
    headers_loaded = fields.Boolean(readonly=True)
    wizard_step = fields.Selection(
        [("files", "Files"), ("mapping", "Mapping"), ("review", "Review")],
        default="files",
        required=True,
    )

    @api.depends("import_seo")
    def _compute_optional_module_status(self):
        module_model = self.env["ir.module.module"].sudo()
        module_states = {
            record["name"]: record["state"]
            for record in module_model.search_read(
                [("name", "in", ["contacts", "website_sale"])],
                ["name", "state"],
            )
        }
        for wizard in self:
            wizard.contacts_installed = module_states.get("contacts") == "installed"
            wizard.website_sale_installed = module_states.get("website_sale") == "installed"
            wizard.product_variants_enabled = self.env.user.has_group("product.group_product_variant")

    @api.depends(
        "contacts_installed",
        "website_sale_installed",
        "product_variants_enabled",
        "import_seo",
        "variation_combination_file_data",
        "variation_definition_file_data",
    )
    def _compute_precheck_message(self):
        for wizard in self:
            messages = []
            if wizard.contacts_installed:
                messages.append(_("`contacts` is installed. Partner imports for manufacturers and suppliers are fully available."))
            else:
                messages.append(
                    _("`contacts` is not installed. The import can still create partners, but the full Contacts UI is unavailable.")
                )
            if wizard.website_sale_installed:
                messages.append(_("`website_sale` is installed. SEO and website fields can be imported."))
            elif wizard.import_seo:
                messages.append(
                    _("`website_sale` is not installed. Disable SEO import or install `website_sale` before validating this run.")
                )
            else:
                messages.append(_("`website_sale` is not installed. SEO fields will stay unavailable unless you install it."))
            if wizard.variation_combination_file_data or wizard.variation_definition_file_data:
                if wizard.product_variants_enabled:
                    messages.append(_("Product variants are enabled. Variation files can be imported."))
                else:
                    messages.append(
                        _("Product variants are not enabled. Activate Odoo variants before validating imports with variation files.")
                    )
            css_class = "alert-warning"
            if (
                wizard.contacts_installed
                and (wizard.website_sale_installed or not wizard.import_seo)
                and (
                    wizard.product_variants_enabled
                    or (not wizard.variation_combination_file_data and not wizard.variation_definition_file_data)
                )
            ):
                css_class = "alert-info"
            wizard.precheck_message = "<div class='alert %s' role='alert'><strong>%s</strong><br/>- %s</div>" % (
                css_class,
                _("Module Precheck"),
                "<br/>- ".join(messages),
            )

    def _get_mapping_lines_for_file(self, file_key):
        self.ensure_one()
        field_map = {
            "article_master": "article_master_mapping_line_ids",
            "manufacturer": "manufacturer_mapping_line_ids",
            "eu_representative": "eu_representative_mapping_line_ids",
            "category": "category_mapping_line_ids",
            "supplierinfo": "supplierinfo_mapping_line_ids",
            "variation_combination": "variation_combination_mapping_line_ids",
            "variation_definition": "variation_definition_mapping_line_ids",
            "attribute": "attribute_mapping_line_ids",
            "feature": "feature_mapping_line_ids",
            "bom": "bom_mapping_line_ids",
        }
        field_name = field_map.get(file_key)
        return self[field_name] if field_name in self._fields else self.mapping_line_ids.filtered(lambda l: l.source_file_key == file_key)

    def _run_module_precheck(self):
        self.ensure_one()
        if self.import_seo and not self.website_sale_installed:
            raise UserError(
                _(
                    "SEO import is enabled, but `website_sale` is not installed. Install `website_sale` first or disable SEO import."
                )
            )
        if (
            (self.variation_combination_file_data or self.variation_definition_file_data)
            and not self.product_variants_enabled
        ):
            raise UserError(
                _(
                    "Variation files were uploaded, but Odoo product variants are not enabled. Activate variants first."
                )
            )

    def _get_import_files(self):
        self.ensure_one()
        file_specs = []
        for file_key, _label in SOURCE_FILE_SELECTION:
            data = getattr(self, "%s_file_data" % file_key)
            filename = getattr(self, "%s_file_name" % file_key)
            if data:
                file_specs.append(
                    {
                        "file_key": file_key,
                        "file_name": filename or ("%s.csv" % file_key),
                        "file_data": data,
                    }
                )
        return file_specs

    def _get_default_mapping_lookup(self, file_key, headers):
        if self.profile_id:
            return {
                line.source_column: line
                for line in self.profile_id.line_ids.filtered(
                    lambda l: l.active and l.source_file_key == file_key and l.source_column in headers
                )
            }
        default_mappings = self.env["jtl.import.mapping"].search(
            [("active", "=", True), ("source_file_key", "=", file_key), ("source_column", "in", headers)],
            order="sequence, id",
        )
        mapping_by_column = {}
        for mapping in default_mappings:
            mapping_by_column.setdefault(mapping.source_column, mapping)
        return mapping_by_column

    def _normalize_header_key(self, header):
        return normalize_header_key(header)

    def _guess_mapping_for_column(self, file_key, header):
        alias = AUTO_MAPPING_ALIASES.get(file_key, {}).get(self._normalize_header_key(header))
        if not alias:
            return {}
        target_model, target_field_name, transform_logic = alias
        target_field_id = False
        if target_model != "website":
            target_field = self.env["ir.model.fields"].search(
                [("model", "=", target_model), ("name", "=", target_field_name)],
                limit=1,
            )
            target_field_id = target_field.id if target_field else False
        return {
            "target_model": target_model,
            "target_field_id": target_field_id,
            "target_field_name": target_field_name,
            "transform_logic": transform_logic,
            "active": True,
        }

    def _build_mapping_lines(self, headers_by_file):
        line_commands = [(5, 0, 0)]
        sequence = 1
        for file_key, _label in SOURCE_FILE_SELECTION:
            headers = headers_by_file.get(file_key) or []
            if not headers:
                continue
            mapping_by_column = self._get_default_mapping_lookup(file_key, headers)
            for header in headers:
                mapping = mapping_by_column.get(header)
                target_field_id = False
                target_field_name = mapping.target_field if mapping else False
                if mapping and mapping.target_model != "website":
                    target_field = self.env["ir.model.fields"].search(
                        [("model", "=", mapping.target_model), ("name", "=", mapping.target_field)],
                        limit=1,
                    )
                    target_field_id = target_field.id if target_field else False
                guessed_mapping = self._guess_mapping_for_column(file_key, header) if not mapping else {}
                line_commands.append(
                    (
                        0,
                        0,
                        {
                            "sequence": sequence * 10,
                            "source_file_key": file_key,
                            "source_column": header,
                            "target_model": mapping.target_model if mapping else guessed_mapping.get("target_model"),
                            "target_field_id": target_field_id or guessed_mapping.get("target_field_id"),
                            "target_field_name": target_field_name or guessed_mapping.get("target_field_name"),
                            "language_code": mapping.language_code if mapping else False,
                            "transform_logic": mapping.transform_logic if mapping else guessed_mapping.get("transform_logic", "text"),
                            "required": mapping.required if mapping else False,
                            "active": bool(mapping) or bool(guessed_mapping.get("active")),
                            "default_value": mapping.default_value if mapping else False,
                        },
                    )
                )
                sequence += 1
        self.mapping_line_ids = line_commands
        self.headers_loaded = True
        self.wizard_step = "mapping"

    def action_load_columns(self):
        self.ensure_one()
        self._run_module_precheck()
        file_specs = self._get_import_files()
        if not file_specs:
            raise UserError(_("Please upload at least one CSV file first."))
        headers_by_file = self.env["jtl.import.parser"].extract_headers_bundle(file_specs)
        self._build_mapping_lines(headers_by_file)
        return self._reopen_wizard()

    def action_apply_profile(self):
        self.ensure_one()
        self._run_module_precheck()
        file_specs = self._get_import_files()
        if not file_specs:
            raise UserError(_("Please upload at least one CSV file first."))
        if not self.profile_id:
            raise UserError(_("Please select a mapping profile first."))
        headers_by_file = self.env["jtl.import.parser"].extract_headers_bundle(file_specs)
        self._build_mapping_lines(headers_by_file)
        return self._reopen_wizard()

    def _reopen_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("New JTL Import"),
            "res_model": "jtl.import.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_previous_step(self):
        self.ensure_one()
        steps = ["files", "mapping", "review"]
        self.wizard_step = steps[max(steps.index(self.wizard_step) - 1, 0)]
        return self._reopen_wizard()

    def action_next_step(self):
        self.ensure_one()
        if self.wizard_step == "files":
            self.action_load_columns()
            return self._reopen_wizard()
        if self.wizard_step == "mapping":
            if not self.mapping_line_ids:
                self.action_load_columns()
            self.wizard_step = "review"
        return self._reopen_wizard()

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
                            "source_file_key": line.source_file_key,
                            "source_column": line.source_column,
                            "target_model": line.target_model,
                            "target_field": line.target_field_id.name or line.target_field_name,
                            "language_code": line.language_id.code if line.language_id else line.language_code,
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
                    "source_file_key": line.source_file_key,
                    "source_column": line.source_column,
                    "target_model": line.target_model,
                    "target_field": target_field,
                    "language_code": (line.language_id.code if line.language_id else line.language_code) or False,
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
        self._run_module_precheck()
        file_specs = self._get_import_files()
        if not file_specs:
            raise UserError(_("Please upload at least one CSV file first."))
        if not self.mapping_line_ids:
            self.action_load_columns()
        self.wizard_step = "review"
        mapping_specs = self._get_selected_mapping_specs()
        self._save_profile_from_lines()
        run_filename = ", ".join(spec["file_name"] for spec in file_specs[:3])
        if len(file_specs) > 3:
            run_filename = _("%s (+%s more)") % (run_filename, len(file_specs) - 3)
        run = self.env["jtl.import.run"].create(
            {
                "filename": run_filename,
                "batch_size": self.batch_size,
                "dry_run": self.dry_run,
                "update_existing_only": self.update_existing_only,
                "import_stock": self.import_stock,
                "import_images": self.import_images,
                "import_gallery_images": self.import_gallery_images,
                "import_seo": self.import_seo,
                "barcode_match_update": self.barcode_match_update,
            }
        )
        run.set_source_bundle(file_specs)
        payload = self.env["jtl.import.parser"].parse_import_bundle(run, file_specs, mapping_specs=mapping_specs)
        run.set_payload(payload)
        run.write(
            {
                "state": "validated",
                "validation_message": _("%s product groups from %s file(s) are staged and ready for queueing.")
                % (len(payload.get("skus", [])), len(file_specs)),
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
    _order = "source_file_key, sequence, id"

    wizard_id = fields.Many2one("jtl.import.wizard", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    source_file_key = fields.Selection(SOURCE_FILE_SELECTION, required=True, default="article_master", readonly=True)
    source_column = fields.Char(required=True, readonly=True)
    target_model = fields.Selection(TARGET_MODEL_SELECTION)
    source_file_label = fields.Char(compute="_compute_source_file_label")
    available_field_ids = fields.Many2many("ir.model.fields", compute="_compute_available_field_ids")
    target_field_id = fields.Many2one("ir.model.fields", string="Odoo Field", domain="[('id', 'in', available_field_ids)]")
    target_field_name = fields.Char(string="Target Field")
    create_field_if_missing = fields.Boolean(string="Create Field")
    relation_hint = fields.Char(compute="_compute_relation_hint")
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
    language_id = fields.Many2one(
        "res.lang",
        string="Sprache",
        compute="_compute_language_id",
        inverse="_inverse_language_id",
        readonly=False,
        domain=[("active", "=", True)],
    )
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

    @api.depends("source_file_key")
    def _compute_source_file_label(self):
        for line in self:
            line.source_file_label = SOURCE_FILE_LABELS.get(line.source_file_key, line.source_file_key)

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

    @api.depends("source_file_key", "source_column", "target_model", "target_field_id", "target_field_name")
    def _compute_relation_hint(self):
        parent_markers = {
            "istvaterartikel",
            "vaterartikelnummer",
            "identifizierungsspaltevaterartikel",
        }
        child_markers = {
            "kindartikelnummer",
            "childsku",
            "kindartikel",
        }
        for line in self:
            normalized = normalize_header_key(line.source_column)
            if line.source_file_key == "article_master" and normalized in parent_markers:
                line.relation_hint = _("Parent marker. Child rows are linked via `Vaterartikelnummer` / `parent_sku`.")
            elif normalized in child_markers:
                line.relation_hint = _("Child article SKU. Used to link variants to the parent.")
            elif line.source_file_key == "supplierinfo" and normalized in {"lieferant", "suppliername", "vendor", "vendorname"}:
                line.relation_hint = _("Supplier matched by name. Supplierinfo will be linked to the vendor partner.")
            elif line.target_field_id and line.target_field_id.name == "parent_sku":
                line.relation_hint = _("Parent SKU detected. This row will be attached to a parent product.")
            elif line.target_field_id and line.target_field_id.name == "is_parent":
                line.relation_hint = _("Parent flag detected. Child products are resolved from the parent reference.")
            else:
                line.relation_hint = False

    @api.onchange("source_column", "create_field_if_missing")
    def _onchange_source_column_generate_field(self):
        for line in self:
            if line.create_field_if_missing and not line.target_field_id and not line.target_field_name and line.source_column:
                sanitized = re.sub(r"[^a-z0-9_]+", "_", line.source_column.strip().lower())
                sanitized = re.sub(r"_+", "_", sanitized).strip("_")
                if sanitized and not sanitized.startswith("x_"):
                    sanitized = "x_%s" % sanitized
                line.target_field_name = sanitized

    @api.depends("language_code")
    def _compute_language_id(self):
        Lang = self.env["res.lang"]
        for line in self:
            line.language_id = Lang.search([("code", "=", line.language_code), ("active", "=", True)], limit=1)

    def _inverse_language_id(self):
        for line in self:
            line.language_code = line.language_id.code if line.language_id else False

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
