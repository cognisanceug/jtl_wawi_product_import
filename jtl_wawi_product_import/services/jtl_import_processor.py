import base64
import hashlib
import logging
from urllib.error import URLError
from urllib.request import urlopen

from odoo import _, models

_logger = logging.getLogger(__name__)


class JtlImportProcessor(models.AbstractModel):
    _name = "jtl.import.processor"
    _description = "JTL Import Processor"

    def _log_skip_create(self, logs, article_number, field_name, message):
        logs.append(
            {
                "article_number": article_number,
                "field_name": field_name,
                "level": "warning",
                "message": message,
            }
        )

    def _get_empty_caches(self):
        return {
            "manufacturer": {},
            "eu_responsible": {},
            "brand": {},
            "category": {},
            "public_category": {},
            "attribute": {},
            "attribute_value": {},
            "tax": {},
            "country": {},
            "location": {},
            "supplierinfo": {},
            "template": {},
            "product": {},
        }

    def _normalize_barcode_value(self, barcode):
        if barcode in (None, False):
            return False
        normalized = str(barcode).strip()
        if not normalized or normalized == "0":
            return False
        return normalized

    def _resolve_unique_barcode(self, run, sku, barcode, logs, current_products=None):
        normalized = self._normalize_barcode_value(barcode)
        if not normalized:
            return False
        product_model = self.env["product.product"].sudo().with_company(run.company_id)
        domain = [
            ("barcode", "=", normalized),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", run.company_id.id),
        ]
        conflicts = product_model.search(domain)
        if current_products:
            current_ids = set(current_products.ids)
            conflicts = conflicts.filtered(lambda product: product.id not in current_ids)
        if conflicts:
            conflict_names = ", ".join(conflicts[:3].mapped("display_name"))
            self._log_skip_create(
                logs,
                sku,
                "barcode",
                _("Barcode %s was ignored because it is already assigned to: %s") % (normalized, conflict_names),
            )
            return False
        return normalized

    def _find_product_by_barcode(self, run, barcode):
        normalized = self._normalize_barcode_value(barcode)
        if not normalized:
            return False
        product_model = self.env["product.product"].sudo().with_company(run.company_id)
        return product_model.search(
            [
                ("barcode", "=", normalized),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", run.company_id.id),
            ],
            limit=1,
        )

    def process_run_batch(self, run):
        self = self.with_context(
            update_existing_only=bool(run.update_existing_only),
            category_root_id=run.category_root_id.id if run.category_root_id else False,
        )
        payload = run.get_payload()
        skus = payload.get("skus", [])
        products = payload.get("products", {})
        manufacturers = payload.get("manufacturers", [])
        eu_responsibles = payload.get("eu_responsibles", [])
        suppliers_master = payload.get("suppliers_master", [])
        if not skus and not (manufacturers or eu_responsibles or suppliers_master):
            run.write({"state": "failed", "last_error": _("No staged records were found for processing.")})
            return False

        run.write({"state": "running"})
        batch_size = min(max(run.batch_size, 1), 500)
        batch_skus = skus[run.current_index : run.current_index + batch_size]
        caches = self._prepare_lookup_caches(run)
        batch_number = run.last_batch_number + 1
        counters = {
            "created_products": 0,
            "updated_products": 0,
            "created_suppliers": 0,
            "updated_suppliers": 0,
            "created_variants": 0,
        }
        all_logs = []

        all_logs.extend(self._process_manufacturer_master_data(run, manufacturers, caches))
        all_logs.extend(self._process_eu_responsible_master_data(run, eu_responsibles, caches))
        all_logs.extend(self._process_supplier_master_data(run, suppliers_master, caches))

        for sku in batch_skus:
            try:
                result = self._process_single_product(run, payload, products.get(sku, {}), caches, batch_number)
                for key in counters:
                    counters[key] += result.get(key, 0)
                all_logs.extend(result.get("logs", []))
            except Exception as exc:  # pragma: no cover - defensive batch isolation
                _logger.exception("Failed processing SKU %s on run %s", sku, run.id)
                all_logs.append(
                    {
                        "batch_number": batch_number,
                        "article_number": sku,
                        "level": "error",
                        "message": _("Unexpected processing error: %s") % exc,
                    }
                )

        run._append_logs(all_logs)
        next_index = run.current_index + len(batch_skus)
        values = {
            "current_index": next_index,
            "last_batch_number": batch_number,
            "created_products": run.created_products + counters["created_products"],
            "updated_products": run.updated_products + counters["updated_products"],
            "created_suppliers": run.created_suppliers + counters["created_suppliers"],
            "updated_suppliers": run.updated_suppliers + counters["updated_suppliers"],
            "created_variants": run.created_variants + counters["created_variants"],
            "state": "done" if next_index >= len(skus) else "queued",
        }
        if values["state"] == "done":
            values["last_error"] = False
        run.write(values)
        return True

    def _prepare_lookup_caches(self, run):
        caches = self._get_empty_caches()
        manufacturer_model = self.env["res.partner"]
        manufacturer_domain = [("is_manufacturer", "=", True)]
        if "is_manufacturer" in manufacturer_model._fields:
            manufacturer_domain = [("is_manufacturer", "=", True)]
        manufacturers = manufacturer_model.search(manufacturer_domain)
        for partner in manufacturers:
            caches["manufacturer"][("name", partner.normalized_name)] = partner
            if partner.email:
                caches["manufacturer"][("email", partner.email.strip().lower())] = partner
            if partner.website:
                caches["manufacturer"][("website", partner.website.strip().lower())] = partner
            if partner.manufacturer_external_id:
                caches["manufacturer"][("external_id", partner.manufacturer_external_id)] = partner
        eu_domain = [("is_eu_responsible", "=", True)]
        eu_responsibles = manufacturer_model.search(eu_domain)
        for partner in eu_responsibles:
            caches["eu_responsible"][("name", partner.normalized_name)] = partner
            if partner.email:
                caches["eu_responsible"][("email", partner.email.strip().lower())] = partner
            if partner.website:
                caches["eu_responsible"][("website", partner.website.strip().lower())] = partner
            if partner.eu_responsible_external_id:
                caches["eu_responsible"][("external_id", partner.eu_responsible_external_id)] = partner
        if "product.brand" in self.env:
            for brand in self.env["product.brand"].search([]):
                caches["brand"][("name", (brand.name or "").strip().lower())] = brand
                if getattr(brand, "brand_external_id", False):
                    caches["brand"][("external_id", brand.brand_external_id)] = brand
        for categ in self.env["product.category"].search([]):
            path = " / ".join(categ.complete_name.split("/")).strip()
            caches["category"][path.lower()] = categ
        if "product.public.category" in self.env:
            for categ in self.env["product.public.category"].search([]):
                path = " / ".join(categ.parent_path_names()).strip() if hasattr(categ, "parent_path_names") else False
                if not path:
                    path = self._build_public_category_path(categ)
                if path:
                    caches["public_category"][path.lower()] = categ
        tax_model = self.env["account.tax"].with_company(run.company_id)
        for tax in tax_model.search([("company_id", "=", run.company_id.id), ("type_tax_use", "=", "sale")]):
            caches["tax"][round(tax.amount, 4)] = tax
        return caches

    def _extract_dynamic_fields(self, model_name, values, excluded_fields=None):
        excluded_fields = set(excluded_fields or [])
        model = self.env[model_name]
        dynamic_vals = {}
        for field_name, value in (values or {}).items():
            if field_name in excluded_fields:
                continue
            if field_name not in model._fields:
                continue
            if value in (None, False, ""):
                continue
            dynamic_vals[field_name] = value
        return dynamic_vals

    def _process_single_product(self, run, payload, product_payload, caches, batch_number):
        product_values = product_payload.get("product", {})
        translations = product_payload.get("translations", {})
        logs = []
        sku = product_payload.get("sku")
        if not sku:
            return {"logs": logs}

        row_number = (product_payload.get("row_numbers") or [False])[0]
        parent_sku = product_values.get("parent_sku")
        is_parent = bool(product_values.get("is_parent"))
        if parent_sku:
            template, created_template, created_variant = self._upsert_variant_product(
                run, payload, product_payload, caches, logs, batch_number
            )
            self._apply_translations(template, translations)
            self._process_suppliers(run, template, product_payload.get("suppliers", []), caches, logs, sku)
            self._process_images(run, template, product_payload.get("images", []), logs)
            if run.import_stock:
                self._process_stock(run, template, product_payload.get("stock", []), caches, logs, sku)
            return {
                "created_products": 1 if created_template else 0,
                "updated_products": 0 if created_template else 1,
                "created_variants": 1 if created_variant else 0,
                "created_suppliers": len([log for log in logs if log.get("field_name") == "supplier_create"]),
                "updated_suppliers": len([log for log in logs if log.get("field_name") == "supplier_update"]),
                "logs": logs,
            }

        template, created = self._upsert_template_product(run, product_payload, caches, logs, batch_number, is_parent=is_parent)
        if not template:
            return {
                "created_products": 0,
                "updated_products": 0,
                "created_variants": 0,
                "created_suppliers": 0,
                "updated_suppliers": 0,
                "logs": logs,
            }
        self._apply_translations(template, translations)
        self._process_suppliers(run, template, product_payload.get("suppliers", []), caches, logs, sku)
        self._process_images(run, template, product_payload.get("images", []), logs)
        if run.import_stock:
            self._process_stock(run, template, product_payload.get("stock", []), caches, logs, sku)
        self._process_bom(run, template, product_payload.get("bom", []), caches, logs, sku)
        if run.import_seo:
            self._process_seo(template, product_payload.get("seo", {}), logs)
        return {
            "created_products": 1 if created else 0,
            "updated_products": 0 if created else 1,
            "created_variants": 0,
            "created_suppliers": len([log for log in logs if log.get("field_name") == "supplier_create"]),
            "updated_suppliers": len([log for log in logs if log.get("field_name") == "supplier_update"]),
            "logs": logs + ([{
                "batch_number": batch_number,
                "row_number": row_number,
                "article_number": sku,
                "level": "info",
                "message": _("Parent product prepared without child variants yet."),
            }] if is_parent else []),
        }

    def _prepare_template_vals(self, run, product_payload, caches, logs, current_products=None):
        values = product_payload.get("product", {})
        sku = product_payload.get("sku")
        raw_rows = product_payload.get("rows") or []
        first_row = raw_rows[0] if raw_rows else {}
        fallback_name = (
            values.get("name")
            or first_row.get("Artikelname")
            or first_row.get("Kind Artikelname")
            or first_row.get("Artikelname (Lieferant)")
            or sku
        )
        template_model_data = product_payload.get("model_data", {}).get("product.template", {})
        template_vals = {
            "name": fallback_name,
            "default_code": sku if "default_code" in self.env["product.template"]._fields else False,
            "barcode": self._resolve_unique_barcode(
                run,
                sku,
                values.get("barcode"),
                logs,
                current_products=current_products,
            ),
            "weight": values.get("weight") or 0.0,
            "length": values.get("length") or 0.0,
            "width": values.get("width") or 0.0,
            "height": values.get("height") or 0.0,
            "active": values.get("active") if "active" in values else True,
            "manufacturer_sku": values.get("manufacturer_sku"),
            "parent_sku": values.get("parent_sku") or sku,
            "seo_path": values.get("seo_path"),
            "standard_price": values.get("purchase_price") or 0.0,
        }
        if "description_sale" in self.env["product.template"]._fields:
            template_vals["description_sale"] = values.get("short_description") or values.get("description")
        if "website_description" in self.env["product.template"]._fields and values.get("description"):
            template_vals["website_description"] = values.get("description")
        elif "description" in self.env["product.template"]._fields and values.get("description"):
            template_vals["description"] = values.get("description")
        category = self._find_or_create_category(values.get("category_path"), caches)
        if category:
            template_vals["categ_id"] = category.id
        manufacturer = self._find_or_create_manufacturer(values, caches)
        if manufacturer:
            template_vals["manufacturer_partner_id"] = manufacturer.id
            if "manufacturer_id" in self.env["product.template"]._fields:
                template_vals["manufacturer_id"] = manufacturer.id
        eu_responsible = self._find_or_create_eu_responsible(values, caches)
        if eu_responsible:
            if "eu_responsible_partner_id" in self.env["product.template"]._fields:
                template_vals["eu_responsible_partner_id"] = eu_responsible.id
        brand = self._find_or_create_brand(values, manufacturer, eu_responsible, caches)
        if brand and "brand_id" in self.env["product.template"]._fields:
            template_vals["brand_id"] = brand.id
        tax = self._map_sale_tax(run, values.get("tax_rate"), caches, logs, sku)
        if tax:
            template_vals["taxes_id"] = [(6, 0, tax.ids)]
        price = values.get("gross_sales_price") or values.get("sale_price")
        if price:
            tax_rate = values.get("tax_rate") or 0.0
            template_vals["list_price"] = round(price / (1 + (tax_rate / 100.0)), 6) if tax_rate else price
        country = self._find_country(values.get("country_of_origin"), caches)
        if country and "country_of_origin" in self.env["product.template"]._fields:
            template_vals["country_of_origin"] = country.id
        if values.get("taric_code") and "hs_code" in self.env["product.template"]._fields:
            template_vals["hs_code"] = values.get("taric_code")
        website_category = self._find_or_create_public_category(product_payload, caches)
        if website_category and "public_categ_ids" in self.env["product.template"]._fields:
            template_vals["public_categ_ids"] = [(4, website_category.id)]
        template_vals.update(
            self._extract_dynamic_fields(
                "product.template",
                template_model_data,
                excluded_fields={
                    "name",
                    "barcode",
                    "weight",
                    "length",
                    "width",
                    "height",
                    "volume",
                    "active",
                    "manufacturer_sku",
                    "parent_sku",
                    "seo_path",
                    "brand_name",
                    "brand_external_id",
                    "manufacturer_name",
                    "manufacturer_email",
                    "manufacturer_website",
                    "manufacturer_external_id",
                    "eu_responsible_name",
                    "eu_responsible_email",
                    "eu_responsible_website",
                    "eu_responsible_external_id",
                    "taric_code",
                    "short_description",
                    "description",
                    "website_description",
                    "purchase_price",
                    "category_path",
                    "gross_sales_price",
                    "sale_price",
                    "country_of_origin",
                },
            )
        )
        return {key: value for key, value in template_vals.items() if value is not False}

    def _upsert_template_product(self, run, product_payload, caches, logs, batch_number, is_parent=False):
        sku = product_payload.get("sku")
        product_model = self.env["product.product"].with_company(run.company_id)
        template_model = self.env["product.template"].with_company(run.company_id)
        existing_variant = product_model.search([("default_code", "=", sku)], limit=1)
        template = existing_variant.product_tmpl_id if existing_variant else template_model.search([("parent_sku", "=", sku)], limit=1)
        if not template and run.barcode_match_update:
            barcode_variant = self._find_product_by_barcode(run, product_payload.get("product", {}).get("barcode"))
            if barcode_variant:
                existing_variant = barcode_variant
                template = barcode_variant.product_tmpl_id
        current_products = template.product_variant_ids if template else False
        vals = self._prepare_template_vals(run, product_payload, caches, logs, current_products=current_products)
        if is_parent:
            vals["parent_sku"] = sku
        if template:
            template.write(vals)
            return template, False
        if run.update_existing_only:
            self._log_skip_create(
                logs,
                sku,
                "product_create",
                _("Product %s does not exist yet and was skipped because 'Update Existing Only' is enabled.") % sku,
            )
            return False, False
        template = template_model.create(vals)
        if template.product_variant_id and not template.product_variant_id.default_code:
            template.product_variant_id.write({"default_code": sku, "parent_sku": vals.get("parent_sku")})
        return template, True

    def _upsert_variant_product(self, run, payload, product_payload, caches, logs, batch_number):
        parent_sku = product_payload.get("product", {}).get("parent_sku")
        parent_payload = payload.get("products", {}).get(parent_sku, {"sku": parent_sku, "product": {"name": parent_sku}})
        template, created_template = self._upsert_template_product(run, parent_payload, caches, logs, batch_number, is_parent=True)
        if not template:
            return False, False, False
        existing_variant = self.env["product.product"].search([("default_code", "=", product_payload.get("sku"))], limit=1)
        barcode_variant = False
        if not existing_variant and run.barcode_match_update:
            barcode_variant = self._find_product_by_barcode(run, product_payload.get("product", {}).get("barcode"))
            if barcode_variant and barcode_variant.product_tmpl_id == template:
                existing_variant = barcode_variant
        attribute_pairs = self._extract_variant_pairs(product_payload.get("attributes", []), product_payload.get("product", {}))
        if not attribute_pairs:
            logs.append(
                {
                    "batch_number": batch_number,
                    "article_number": product_payload.get("sku"),
                    "field_name": "variant",
                    "level": "warning",
                    "message": _("Variant row is missing attribute structure and was processed as a simple SKU update."),
                }
            )
            variant = self.env["product.product"].search([("default_code", "=", product_payload.get("sku"))], limit=1)
            if variant:
                variant.write({"parent_sku": parent_sku})
                return template, created_template, False
            return template, created_template, False

        ptav_records = self._ensure_template_variant_structure(template, attribute_pairs, caches)
        template._create_variant_ids()
        variant = self.env["product.product"].search([("default_code", "=", product_payload.get("sku"))], limit=1)
        if not variant:
            wanted_value_ids = set(ptav_records.mapped("product_attribute_value_id").ids)
            for candidate in template.product_variant_ids:
                candidate_value_ids = set(candidate.product_template_attribute_value_ids.mapped("product_attribute_value_id").ids)
                if candidate_value_ids == wanted_value_ids:
                    variant = candidate
                    break
        if not variant and existing_variant and existing_variant.product_tmpl_id == template:
            variant = existing_variant
        created_variant = False
        if variant:
            created_variant = not bool(existing_variant)
            current_products = variant if variant else template.product_variant_ids
            variant_vals = {
                "default_code": product_payload.get("sku"),
                "barcode": self._resolve_unique_barcode(
                    run,
                    product_payload.get("sku"),
                    product_payload.get("product", {}).get("barcode"),
                    logs,
                    current_products=current_products,
                ),
                "weight": product_payload.get("product", {}).get("weight") or 0.0,
                "parent_sku": parent_sku,
                "active": product_payload.get("product", {}).get("active", True),
            }
            variant_vals.update(
                self._extract_dynamic_fields(
                    "product.product",
                    product_payload.get("model_data", {}).get("product.product", {}),
                    excluded_fields={
                        "default_code",
                        "barcode",
                        "weight",
                        "parent_sku",
                        "is_parent",
                        "variant_attribute_1_name",
                        "variant_attribute_1_value",
                        "variant_attribute_2_name",
                        "variant_attribute_2_value",
                        "variant_attribute_3_name",
                        "variant_attribute_3_value",
                    },
                )
            )
            variant.write(variant_vals)
        elif run.update_existing_only:
            self._log_skip_create(
                logs,
                product_payload.get("sku"),
                "variant_create",
                _("Variant %s does not exist yet and was skipped because 'Update Existing Only' is enabled.") % product_payload.get("sku"),
            )
            return template, created_template, False
        template.write(
            self._prepare_template_vals(
                run,
                product_payload,
                caches,
                logs,
                current_products=template.product_variant_ids,
            )
        )
        return template, created_template, created_variant

    def _extract_variant_pairs(self, attribute_rows, product_values):
        pairs = []
        for index in range(1, 4):
            name = product_values.get("variant_attribute_%s_name" % index)
            value = product_values.get("variant_attribute_%s_value" % index)
            if name and value:
                pairs.append((name, value, True))
        for attribute_row in attribute_rows:
            name = attribute_row.get("name")
            value = attribute_row.get("value")
            is_variant = bool(attribute_row.get("is_variant"))
            if name and value:
                pairs.append((name, value, is_variant))
        return pairs

    def _ensure_template_variant_structure(self, template, attribute_pairs, caches):
        ptav_model = self.env["product.template.attribute.value"]
        line_model = self.env["product.template.attribute.line"]
        ptav_records = ptav_model.browse()
        for name, value, is_variant in attribute_pairs:
            attribute = self._find_or_create_attribute(name, caches, is_variant=is_variant)
            if not attribute:
                continue
            attr_value = self._find_or_create_attribute_value(attribute, value, caches)
            if not attr_value:
                continue
            line = template.attribute_line_ids.filtered(lambda l: l.attribute_id == attribute)[:1]
            if not line:
                line = line_model.create(
                    {
                        "product_tmpl_id": template.id,
                        "attribute_id": attribute.id,
                        "value_ids": [(4, attr_value.id)],
                    }
                )
            elif attr_value not in line.value_ids:
                line.write({"value_ids": [(4, attr_value.id)]})
            ptav = line.product_template_value_ids.filtered(lambda rec: rec.product_attribute_value_id == attr_value)[:1]
            if not ptav:
                ptav = ptav_model.search(
                    [("attribute_line_id", "=", line.id), ("product_attribute_value_id", "=", attr_value.id)],
                    limit=1,
                )
            ptav_records |= ptav
        return ptav_records

    def _find_or_create_partner_by_role(self, values, caches, role):
        role_prefix = "manufacturer" if role == "manufacturer" else "eu_responsible"
        normalized = self.env["jtl.import.normalizer"].normalized_name(values.get("%s_name" % role_prefix))
        email = (values.get("%s_email" % role_prefix) or "").strip().lower()
        website = (values.get("%s_website" % role_prefix) or "").strip().lower()
        external_id = values.get("%s_external_id" % role_prefix)
        cache_key = "manufacturer" if role == "manufacturer" else "eu_responsible"
        external_field = "manufacturer_external_id" if role == "manufacturer" else "eu_responsible_external_id"
        flag_field = "is_manufacturer" if role == "manufacturer" else "is_eu_responsible"
        business_flag_field = "is_manufacturer" if role == "manufacturer" else "is_eu_responsible"
        for key in (
            ("external_id", external_id),
            ("email", email),
            ("website", website),
            ("name", normalized),
        ):
            if key[1] and caches[cache_key].get(key):
                return caches[cache_key][key]
        if not normalized:
            return False
        domain = [("is_company", "=", True), ("normalized_name", "=", normalized)]
        if external_id:
            partner = self.env["res.partner"].search([(external_field, "=", external_id)], limit=1)
            if partner:
                caches[cache_key][("external_id", external_id)] = partner
                return partner
        partner = self.env["res.partner"].search(domain, limit=1)
        if not partner and email:
            partner = self.env["res.partner"].search([("email", "=ilike", email), ("is_company", "=", True)], limit=1)
        if not partner and website:
            partner = self.env["res.partner"].search([("website", "=ilike", website), ("is_company", "=", True)], limit=1)
        if not partner:
            if self.env.context.get("update_existing_only"):
                return False
            partner_vals = {
                "name": values.get("%s_name" % role_prefix),
                "is_company": True,
                "email": values.get("%s_email" % role_prefix),
                "website": values.get("%s_website" % role_prefix),
                flag_field: True,
                external_field: external_id,
            }
            if business_flag_field in self.env["res.partner"]._fields:
                partner_vals[business_flag_field] = True
            if role == "manufacturer":
                tag = self.env["res.partner.category"].search([("is_manufacturer_tag", "=", True)], limit=1)
                if not tag:
                    tag = self.env["res.partner.category"].create({"name": "Manufacturer", "is_manufacturer_tag": True})
                partner_vals["category_id"] = [(4, tag.id)]
            partner_vals.update(
                self._extract_dynamic_fields(
                    "res.partner",
                    values,
                    excluded_fields={
                        "%s_name" % role_prefix,
                        "%s_email" % role_prefix,
                        "%s_website" % role_prefix,
                        "%s_external_id" % role_prefix,
                    },
                )
            )
            partner = self.env["res.partner"].create(partner_vals)
        else:
            write_vals = {flag_field: True}
            if business_flag_field in self.env["res.partner"]._fields:
                write_vals[business_flag_field] = True
            if external_id and not getattr(partner, external_field, False):
                write_vals[external_field] = external_id
            if write_vals:
                partner.write(write_vals)
        caches[cache_key][("name", normalized)] = partner
        if email:
            caches[cache_key][("email", email)] = partner
        if website:
            caches[cache_key][("website", website)] = partner
        if external_id:
            caches[cache_key][("external_id", external_id)] = partner
        return partner

    def _find_or_create_manufacturer(self, values, caches):
        return self._find_or_create_partner_by_role(values, caches, "manufacturer")

    def _find_or_create_eu_responsible(self, values, caches):
        return self._find_or_create_partner_by_role(values, caches, "eu_responsible")

    def _find_or_create_brand(self, values, manufacturer, eu_responsible, caches):
        if "product.brand" not in self.env:
            return False
        brand_name = (values.get("brand_name") or "").strip()
        external_id = values.get("brand_external_id")
        if external_id and caches["brand"].get(("external_id", external_id)):
            brand = caches["brand"][("external_id", external_id)]
        elif brand_name and caches["brand"].get(("name", brand_name.lower())):
            brand = caches["brand"][("name", brand_name.lower())]
        elif not brand_name and not external_id:
            return False
        else:
            domain = []
            if external_id and "brand_external_id" in self.env["product.brand"]._fields:
                domain = [("brand_external_id", "=", external_id)]
            if not domain and brand_name:
                domain = [("name", "=ilike", brand_name)]
            brand = self.env["product.brand"].search(domain, limit=1) if domain else self.env["product.brand"]
            if not brand:
                if self.env.context.get("update_existing_only") or not brand_name:
                    return False
                create_vals = {"name": brand_name}
                if manufacturer:
                    create_vals["manufacturer_id"] = manufacturer.id
                if eu_responsible and "gdpr_responsible_id" in self.env["product.brand"]._fields:
                    create_vals["gdpr_responsible_id"] = eu_responsible.id
                if external_id and "brand_external_id" in self.env["product.brand"]._fields:
                    create_vals["brand_external_id"] = external_id
                brand = self.env["product.brand"].create(create_vals)
            else:
                write_vals = {}
                if manufacturer and "manufacturer_id" in brand._fields and brand.manufacturer_id != manufacturer:
                    write_vals["manufacturer_id"] = manufacturer.id
                if eu_responsible and "gdpr_responsible_id" in brand._fields and brand.gdpr_responsible_id != eu_responsible:
                    write_vals["gdpr_responsible_id"] = eu_responsible.id
                if external_id and "brand_external_id" in brand._fields and not brand.brand_external_id:
                    write_vals["brand_external_id"] = external_id
                if write_vals:
                    brand.write(write_vals)
        if brand_name:
            caches["brand"][("name", brand_name.lower())] = brand
        if external_id:
            caches["brand"][("external_id", external_id)] = brand
        return brand

    def _process_manufacturer_master_data(self, run, manufacturer_rows, caches):
        logs = []
        for row in manufacturer_rows or []:
            partner_data = dict((row.get("model_data") or {}).get("res.partner") or {})
            manufacturer_name = partner_data.get("name") or row.get("name")
            if not manufacturer_name:
                continue
            values = {
                "manufacturer_name": manufacturer_name,
                "manufacturer_email": partner_data.get("email"),
                "manufacturer_website": partner_data.get("website"),
                "manufacturer_external_id": partner_data.get("manufacturer_external_id"),
                **partner_data,
            }
            partner = self._find_or_create_manufacturer(values, caches)
            if not partner:
                if run.update_existing_only:
                    self._log_skip_create(
                        logs,
                        False,
                        "manufacturer_create",
                        _("Manufacturer %s was skipped because 'Update Existing Only' is enabled.") % manufacturer_name,
                    )
                continue
            write_vals = self._extract_dynamic_fields(
                "res.partner",
                partner_data,
                excluded_fields={"name", "email", "website", "manufacturer_external_id", "is_manufacturer"},
            )
            if partner_data.get("email") and partner.email != partner_data.get("email"):
                write_vals["email"] = partner_data.get("email")
            if partner_data.get("website") and partner.website != partner_data.get("website"):
                write_vals["website"] = partner_data.get("website")
            if partner_data.get("name") and partner.name != partner_data.get("name"):
                write_vals["name"] = partner_data.get("name")
            if write_vals:
                partner.write(write_vals)
            logs.append(
                {
                    "article_number": False,
                    "field_name": "manufacturer_master",
                    "level": "info",
                    "message": _("Manufacturer prepared: %s") % partner.display_name,
                }
            )
        return logs

    def _process_eu_responsible_master_data(self, run, eu_rows, caches):
        logs = []
        for row in eu_rows or []:
            partner_data = dict((row.get("model_data") or {}).get("res.partner") or {})
            partner_name = partner_data.get("name") or row.get("name")
            if not partner_name:
                continue
            values = {
                "eu_responsible_name": partner_name,
                "eu_responsible_email": partner_data.get("email"),
                "eu_responsible_website": partner_data.get("website"),
                "eu_responsible_external_id": partner_data.get("eu_responsible_external_id"),
                **partner_data,
            }
            partner = self._find_or_create_eu_responsible(values, caches)
            if not partner:
                if run.update_existing_only:
                    self._log_skip_create(
                        logs,
                        False,
                        "eu_responsible_create",
                        _("EU representative %s was skipped because 'Update Existing Only' is enabled.") % partner_name,
                    )
                continue
            write_vals = self._extract_dynamic_fields(
                "res.partner",
                partner_data,
                excluded_fields={"name", "email", "website", "eu_responsible_external_id", "is_eu_responsible"},
            )
            if "is_eu_responsible" in self.env["res.partner"]._fields:
                write_vals["is_eu_responsible"] = True
            if partner_data.get("email") and partner.email != partner_data.get("email"):
                write_vals["email"] = partner_data.get("email")
            if partner_data.get("website") and partner.website != partner_data.get("website"):
                write_vals["website"] = partner_data.get("website")
            if partner_data.get("name") and partner.name != partner_data.get("name"):
                write_vals["name"] = partner_data.get("name")
            if write_vals:
                partner.write(write_vals)
            logs.append(
                {
                    "article_number": False,
                    "field_name": "eu_responsible_master",
                    "level": "info",
                    "message": _("EU representative prepared: %s") % partner.display_name,
                }
            )
        return logs

    # JTL-Wawi exports country codes like "D" / "A" / "CH"; map the common
    # single-letter DACH codes to ISO so res.country lookups succeed.
    _JTL_COUNTRY_ALIASES = {
        "d": "DE", "de": "DE", "deutschland": "DE", "germany": "DE",
        "a": "AT", "at": "AT", "oesterreich": "AT", "österreich": "AT", "austria": "AT",
        "ch": "CH", "schweiz": "CH", "switzerland": "CH",
    }

    def _resolve_supplier_country(self, value, caches):
        if not value:
            return False
        normalized = str(value).strip()
        iso = self._JTL_COUNTRY_ALIASES.get(normalized.lower(), normalized)
        return self._find_country(iso, caches)

    def _process_supplier_master_data(self, run, supplier_rows, caches):
        """Import the JTL "Lieferantenstammdaten" file as res.partner contacts
        and mark them as vendors (supplier_rank >= 1).

        Idempotent / no duplicates: an existing partner is reused — never
        re-created — when it matches on any of, in priority order:
          1. ref (JTL Lieferantennummer) — reliable on re-import
          2. vat / USt-IdNr.      — strong business key, also catches partners
                                    that already existed before the first import
          3. email                — every JTL supplier row carries one
          4. normalized company name (case-/whitespace-insensitive)
        Only when none match is a new contact created.

        We deliberately reuse the stock ``res.partner.ref`` field instead of
        adding a custom column: a new stored column on res.partner collides
        with Odoo's ``button_install`` handler (it reads res.partner before
        migrations run), see migrations/19.0.1.0.7."""
        logs = []
        Partner = self.env["res.partner"]
        normalizer = self.env["jtl.import.normalizer"]
        for row in supplier_rows or []:
            partner_data = dict((row.get("model_data") or {}).get("res.partner") or {})
            supplier_name = partner_data.get("name") or row.get("name")
            if not supplier_name:
                continue
            external_id = partner_data.get("ref") or row.get("external_id")

            # country_id may arrive as a raw string ("D", "Deutschland") —
            # resolve it to a res.country id before it reaches write()/create().
            country_value = partner_data.get("country_id")
            if country_value and not isinstance(country_value, int):
                country = self._resolve_supplier_country(country_value, caches)
                if country:
                    partner_data["country_id"] = country.id
                else:
                    partner_data.pop("country_id", None)
                    self._log_skip_create(
                        logs,
                        external_id or supplier_name,
                        "country_id",
                        _("Country '%s' could not be resolved and was skipped.") % country_value,
                    )

            vat = (partner_data.get("vat") or "").strip()
            email = (partner_data.get("email") or "").strip()

            # Match an existing contact instead of creating a duplicate.
            # A fresh search() per row (no cache) guarantees we also see
            # partners created earlier in the same batch.
            partner = False
            if external_id:
                partner = Partner.search(
                    [("is_company", "=", True), ("ref", "=", external_id)], limit=1
                )
            if not partner and vat:
                partner = Partner.search(
                    [("is_company", "=", True), ("vat", "=ilike", vat)], limit=1
                )
            if not partner and email:
                partner = Partner.search(
                    [("is_company", "=", True), ("email", "=ilike", email)], limit=1
                )
            if not partner:
                normalized = normalizer.normalized_name(supplier_name)
                partner = Partner.search(
                    [("is_company", "=", True), ("normalized_name", "=", normalized)], limit=1
                )

            write_vals = self._extract_dynamic_fields("res.partner", partner_data)
            write_vals["is_company"] = True

            if partner:
                # Never downgrade an already-ranked vendor; just ensure rank >= 1.
                if partner.supplier_rank < 1:
                    write_vals["supplier_rank"] = 1
                partner.write(write_vals)
                logs.append(
                    {
                        "article_number": external_id or False,
                        "field_name": "supplier_master_update",
                        "level": "info",
                        "message": _("Supplier updated: %s") % partner.display_name,
                    }
                )
            else:
                if run.update_existing_only:
                    self._log_skip_create(
                        logs,
                        external_id or False,
                        "supplier_master_create",
                        _("Supplier %s was skipped because 'Update Existing Only' is enabled.") % supplier_name,
                    )
                    continue
                write_vals["supplier_rank"] = 1
                partner = Partner.create(write_vals)
                logs.append(
                    {
                        "article_number": external_id or False,
                        "field_name": "supplier_master_create",
                        "level": "info",
                        "message": _("Supplier created: %s") % partner.display_name,
                    }
                )
        return logs

    def _find_or_create_category(self, category_path, caches):
        if not category_path:
            return False
        path = str(category_path).replace(">", "/")
        parts = [item.strip() for item in path.split("/") if item.strip()]
        if not parts:
            return False
        cache_key = " / ".join(parts).lower()
        if caches["category"].get(cache_key):
            return caches["category"][cache_key]
        root_id = self.env.context.get("category_root_id") or False
        root = self.env["product.category"].browse(root_id) if root_id else False
        parent = root if root and root.exists() else False
        full_path_parts = []
        if parent:
            full_path_parts = [parent.name]
        for part in parts:
            full_path_parts.append(part)
            part_key = " / ".join(full_path_parts).lower()
            category = caches["category"].get(part_key)
            if not category:
                domain = [("name", "=", part), ("parent_id", "=", parent.id if parent else False)]
                category = self.env["product.category"].search(domain, limit=1)
                if not category:
                    if self.env.context.get("update_existing_only"):
                        return parent if parent and parent != root else False
                    category = self.env["product.category"].create({"name": part, "parent_id": parent.id if parent else False})
                caches["category"][part_key] = category
            parent = category
        return parent if parent and parent != root else parent

    def _build_public_category_path(self, category):
        names = []
        current = category
        while current:
            names.append(current.name)
            current = current.parent_id
        return " / ".join(reversed(names))

    def _find_or_create_public_category(self, product_payload, caches):
        if "product.public.category" not in self.env:
            return False
        values = product_payload.get("product", {})
        category_path = values.get("category_path")
        if not category_path:
            return False
        public_category_model = self.env["product.public.category"]
        path = str(category_path).replace(">", "/")
        path_parts = [item.strip() for item in path.split("/") if item.strip()]
        if not path_parts:
            return False
        seo_data = product_payload.get("seo", {}) or {}
        public_name = seo_data.get("public_category_name")
        translated_names = seo_data.get("public_category_name_translations") or {}
        cache_key = " / ".join(path_parts).lower()
        if caches["public_category"].get(cache_key):
            category = caches["public_category"][cache_key]
            if public_name and category.name != public_name:
                category.name = public_name
            for lang_code, value in translated_names.items():
                if value:
                    category.with_context(lang=lang_code).name = value
            return category
        parent = False
        full_path_parts = []
        for index, part in enumerate(path_parts):
            full_path_parts.append(part)
            part_key = " / ".join(full_path_parts).lower()
            category = caches["public_category"].get(part_key)
            desired_name = public_name if index == len(path_parts) - 1 and public_name else part
            if not category:
                domain = [("name", "=", desired_name), ("parent_id", "=", parent.id if parent else False)]
                category = public_category_model.search(domain, limit=1)
                if not category and desired_name != part:
                    domain = [("name", "=", part), ("parent_id", "=", parent.id if parent else False)]
                    category = public_category_model.search(domain, limit=1)
                if not category:
                    if self.env.context.get("update_existing_only"):
                        return parent
                    category = public_category_model.create({"name": desired_name, "parent_id": parent.id if parent else False})
                elif category.name != desired_name:
                    category.name = desired_name
                caches["public_category"][part_key] = category
            parent = category
        if parent and translated_names:
            for lang_code, value in translated_names.items():
                if value:
                    parent.with_context(lang=lang_code).name = value
        return parent

    def _find_country(self, name, caches):
        if not name:
            return False
        key = str(name).strip().lower()
        if caches["country"].get(key):
            return caches["country"][key]
        country = self.env["res.country"].search(["|", ("name", "=ilike", name), ("code", "=ilike", name)], limit=1)
        if country:
            caches["country"][key] = country
        return country

    def _find_or_create_attribute(self, name, caches, is_variant=False):
        key = (name or "").strip().lower()
        if caches["attribute"].get(key):
            return caches["attribute"][key]
        attribute = self.env["product.attribute"].search([("name", "=ilike", name)], limit=1)
        if not attribute:
            if self.env.context.get("update_existing_only"):
                return False
            vals = {"name": name}
            if "create_variant" in self.env["product.attribute"]._fields:
                vals["create_variant"] = "always" if is_variant else "no_variant"
            attribute = self.env["product.attribute"].create(vals)
        caches["attribute"][key] = attribute
        return attribute

    def _find_or_create_attribute_value(self, attribute, value, caches):
        key = (attribute.id, (value or "").strip().lower())
        if caches["attribute_value"].get(key):
            return caches["attribute_value"][key]
        attr_value = self.env["product.attribute.value"].search(
            [("attribute_id", "=", attribute.id), ("name", "=ilike", value)],
            limit=1,
        )
        if not attr_value:
            if self.env.context.get("update_existing_only"):
                return False
            attr_value = self.env["product.attribute.value"].create({"attribute_id": attribute.id, "name": value})
        caches["attribute_value"][key] = attr_value
        return attr_value

    def _map_sale_tax(self, run, tax_rate, caches, logs, sku):
        if tax_rate in (None, False, ""):
            return False
        rounded_rate = round(float(tax_rate), 4)
        tax = caches["tax"].get(rounded_rate)
        if tax:
            return tax
        logs.append(
            {
                "article_number": sku,
                "field_name": "tax_rate",
                "level": "warning",
                "message": _("No sales tax found for rate %s%% in company %s.") % (rounded_rate, run.company_id.display_name),
            }
        )
        return False

    def _apply_translations(self, template, translations):
        language_map = {
            "de": "de_DE",
            "de_de": "de_DE",
            "en": "en_US",
            "en_us": "en_US",
        }
        for field_name, lang_values in (translations or {}).items():
            odoo_field = {
                "name": "name",
                "short_description": "description_sale",
                "meta_title": "meta_title",
                "meta_description": "meta_description",
            }.get(field_name)
            if field_name == "description":
                if "website_description" in template._fields:
                    odoo_field = "website_description"
                elif "description" in template._fields:
                    odoo_field = "description"
                elif "description_sale" in template._fields:
                    odoo_field = "description_sale"
            if not odoo_field or odoo_field not in template._fields:
                continue
            for lang_code, value in (lang_values or {}).items():
                if not value:
                    continue
                template.with_context(lang=language_map.get(lang_code.lower(), lang_code)).write({odoo_field: value})

    def _process_suppliers(self, run, template, supplier_rows, caches, logs, sku):
        supplierinfo_model = self.env["product.supplierinfo"]
        partner_field = "partner_id" if "partner_id" in supplierinfo_model._fields else "name"
        template_field = "product_tmpl_id" if "product_tmpl_id" in supplierinfo_model._fields else False
        product_field = "product_id" if "product_id" in supplierinfo_model._fields else False

        for supplier_data in supplier_rows:
            supplier_name = supplier_data.get("supplier_name")
            if not supplier_name:
                continue
            vendor = self.env["res.partner"].search([("name", "=ilike", supplier_name), ("is_company", "=", True)], limit=1)
            if not vendor:
                if run.update_existing_only:
                    self._log_skip_create(
                        logs,
                        sku,
                        "supplier_create",
                        _("Supplier %s was skipped because 'Update Existing Only' is enabled.") % supplier_name,
                    )
                    continue
                vendor = self.env["res.partner"].create({"name": supplier_name, "is_company": True, "supplier_rank": 1})
            domain = [(partner_field, "=", vendor.id)]
            if template_field:
                domain.append((template_field, "=", template.id))
            elif product_field and template.product_variant_id:
                domain.append((product_field, "=", template.product_variant_id.id))
            if "product_code" in supplierinfo_model._fields:
                domain.append(("product_code", "=", supplier_data.get("supplier_product_number") or False))
            supplierinfo = supplierinfo_model.search(domain, limit=1)

            vals = {
                partner_field: vendor.id,
                "price": supplier_data.get("purchase_price") or 0.0,
                "delay": int(supplier_data.get("lead_time") or 0),
                "min_qty": supplier_data.get("minimum_quantity") or 0.0,
                "is_default_supplier": supplier_data.get("default_supplier"),
            }
            if template_field:
                vals[template_field] = template.id
            if product_field and template.product_variant_id:
                vals[product_field] = template.product_variant_id.id
            if "product_code" in supplierinfo_model._fields:
                vals["product_code"] = supplier_data.get("supplier_product_number")
            if supplierinfo:
                supplierinfo.write(vals)
                logs.append({"article_number": sku, "field_name": "supplier_update", "level": "info", "message": _("Supplier updated: %s") % vendor.name})
            else:
                if run.update_existing_only:
                    self._log_skip_create(
                        logs,
                        sku,
                        "supplierinfo_create",
                        _("Supplierinfo for %s was skipped because 'Update Existing Only' is enabled.") % vendor.name,
                    )
                    continue
                supplierinfo_model.create(vals)
                logs.append({"article_number": sku, "field_name": "supplier_create", "level": "info", "message": _("Supplier created: %s") % vendor.name})

    def _process_images(self, run, template, image_rows, logs):
        if not run.import_images:
            return
        gallery_supported = "product.image" in self.env
        seen_checksums = set()
        seen_urls = set()
        for index, image_data in enumerate(image_rows):
            source_url = image_data.get("image_url") or image_data.get("url")
            if not source_url:
                continue
            if source_url in seen_urls:
                continue
            try:
                response = urlopen(source_url, timeout=10)
                content = response.read()
            except (URLError, ValueError) as exc:
                logs.append({"article_number": template.parent_sku, "field_name": "image_url", "level": "warning", "message": _("Image download failed for %s: %s") % (source_url, exc)})
                continue
            checksum = hashlib.sha1(content).hexdigest()
            if checksum in seen_checksums:
                continue
            seen_urls.add(source_url)
            seen_checksums.add(checksum)
            if index == 0 and not template.image_1920:
                template.image_1920 = base64.b64encode(content)
            elif run.import_gallery_images and gallery_supported:
                self.env["product.image"].create(
                    {
                        "name": template.name,
                        "product_tmpl_id": template.id,
                        "image_1920": base64.b64encode(content),
                    }
                )
            elif run.import_gallery_images and not gallery_supported:
                logs.append(
                    {
                        "article_number": template.parent_sku,
                        "field_name": "image_url",
                        "level": "warning",
                        "message": _("Gallery image import skipped because product.image is unavailable in this Odoo instance."),
                    }
                )
                return

    def _process_stock(self, run, template, stock_rows, caches, logs, sku):
        quant_model = self.env["stock.quant"]
        inventory_location = self.env["stock.location"].search([("usage", "=", "internal")], limit=1)
        for stock_data in stock_rows:
            quantity = stock_data.get("quantity")
            if quantity in (None, False, ""):
                continue
            location_name = stock_data.get("location")
            location = inventory_location
            if location_name:
                location = self.env["stock.location"].search([("complete_name", "=ilike", location_name)], limit=1) or inventory_location
            if not location:
                logs.append({"article_number": sku, "field_name": "stock", "level": "warning", "message": _("No internal stock location is available.")})
                return
            quant = quant_model.search([("product_id", "=", template.product_variant_id.id), ("location_id", "=", location.id)], limit=1)
            if quant:
                quant.inventory_quantity = quantity
                quant.action_apply_inventory()
            else:
                if run.update_existing_only:
                    self._log_skip_create(
                        logs,
                        sku,
                        "stock_quant_create",
                        _("Stock quant for %s at %s was skipped because 'Update Existing Only' is enabled.") % (sku, location.display_name),
                    )
                    continue
                quant = quant_model.create({"product_id": template.product_variant_id.id, "location_id": location.id, "inventory_quantity": quantity})
                quant.action_apply_inventory()

    def _process_seo(self, template, seo_data, logs):
        meta_title = seo_data.get("meta_title")
        if isinstance(meta_title, dict):
            meta_title = meta_title.get("de_DE") or meta_title.get("de") or next(iter(meta_title.values()), False)
        meta_description = seo_data.get("meta_description")
        if isinstance(meta_description, dict):
            meta_description = meta_description.get("de_DE") or meta_description.get("de") or next(iter(meta_description.values()), False)
        if "website_published" in template._fields and seo_data.get("website_published") is not None:
            template.website_published = seo_data.get("website_published")
        if "website_meta_title" in template._fields and meta_title:
            template.website_meta_title = meta_title
        if "website_meta_description" in template._fields and meta_description:
            template.website_meta_description = meta_description
        if "website_description" in template._fields and seo_data.get("description"):
            template.website_description = seo_data.get("description")
        if "website_published" not in template._fields and seo_data:
            logs.append({"article_number": template.parent_sku, "field_name": "website", "level": "warning", "message": _("Website fields are unavailable because website_sale is not installed.")})

    def _process_bom(self, run, template, bom_rows, caches, logs, sku):
        if not bom_rows:
            return
        if "mrp.bom" not in self.env:
            logs.append(
                {
                    "article_number": sku,
                    "field_name": "bom",
                    "level": "warning",
                    "message": _("BOM rows were staged but MRP is not installed, so no Stückliste was created."),
                }
            )
            return
        bom_model = self.env["mrp.bom"]
        bom_line_model = self.env["mrp.bom.line"]
        component_model = self.env["product.product"]
        bom = bom_model.search([("product_tmpl_id", "=", template.id), ("type", "=", "normal")], limit=1)
        if not bom:
            if run.update_existing_only:
                self._log_skip_create(
                    logs,
                    sku,
                    "bom_create",
                    _("BOM for %s was skipped because 'Update Existing Only' is enabled.") % sku,
                )
                return
            bom = bom_model.create({"product_tmpl_id": template.id, "type": "normal"})
        existing_by_product = {line.product_id.id: line for line in bom.bom_line_ids}
        desired_product_ids = set()
        for row in bom_rows:
            component_sku = row.get("component_sku")
            if not component_sku:
                continue
            component = caches["product"].get(component_sku) or component_model.search([("default_code", "=", component_sku)], limit=1)
            if not component:
                logs.append(
                    {
                        "article_number": sku,
                        "field_name": "bom",
                        "level": "warning",
                        "message": _("BOM component %s was not found in Odoo.") % component_sku,
                    }
                )
                continue
            caches["product"][component_sku] = component
            desired_product_ids.add(component.id)
            quantity = row.get("quantity") or 1.0
            line_vals = {"bom_id": bom.id, "product_id": component.id, "product_qty": quantity}
            existing_line = existing_by_product.get(component.id)
            if existing_line:
                existing_line.write(line_vals)
            else:
                if run.update_existing_only:
                    self._log_skip_create(
                        logs,
                        sku,
                        "bom_line_create",
                        _("BOM line %s -> %s was skipped because 'Update Existing Only' is enabled.") % (sku, component_sku),
                    )
                    continue
                bom_line_model.create(line_vals)
        stale_lines = bom.bom_line_ids.filtered(lambda line: line.product_id.id not in desired_product_ids)
        if stale_lines:
            stale_lines.unlink()
