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

    def _get_empty_caches(self):
        return {
            "manufacturer": {},
            "category": {},
            "attribute": {},
            "attribute_value": {},
            "tax": {},
            "country": {},
            "location": {},
            "supplierinfo": {},
            "template": {},
            "product": {},
        }

    def process_run_batch(self, run):
        payload = run.get_payload()
        skus = payload.get("skus", [])
        products = payload.get("products", {})
        if not skus:
            run.write({"state": "failed", "last_error": _("No staged products were found for processing.")})
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
        manufacturers = manufacturer_model.search([("is_jtl_manufacturer", "=", True)])
        for partner in manufacturers:
            caches["manufacturer"][("name", partner.jtl_normalized_name)] = partner
            if partner.email:
                caches["manufacturer"][("email", partner.email.strip().lower())] = partner
            if partner.website:
                caches["manufacturer"][("website", partner.website.strip().lower())] = partner
            if partner.jtl_manufacturer_external_id:
                caches["manufacturer"][("external_id", partner.jtl_manufacturer_external_id)] = partner
        for categ in self.env["product.category"].search([]):
            path = " / ".join(categ.complete_name.split("/")).strip()
            caches["category"][path.lower()] = categ
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
                self._process_stock(template, product_payload.get("stock", []), caches, logs, sku)
            return {
                "created_products": 1 if created_template else 0,
                "updated_products": 0 if created_template else 1,
                "created_variants": 1 if created_variant else 0,
                "created_suppliers": len([log for log in logs if log.get("field_name") == "supplier_create"]),
                "updated_suppliers": len([log for log in logs if log.get("field_name") == "supplier_update"]),
                "logs": logs,
            }

        template, created = self._upsert_template_product(run, product_payload, caches, logs, batch_number, is_parent=is_parent)
        self._apply_translations(template, translations)
        self._process_suppliers(run, template, product_payload.get("suppliers", []), caches, logs, sku)
        self._process_images(run, template, product_payload.get("images", []), logs)
        if run.import_stock:
            self._process_stock(template, product_payload.get("stock", []), caches, logs, sku)
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

    def _prepare_template_vals(self, run, product_payload, caches, logs):
        values = product_payload.get("product", {})
        sku = product_payload.get("sku")
        template_model_data = product_payload.get("model_data", {}).get("product.template", {})
        template_vals = {
            "name": values.get("name") or sku,
            "default_code": sku if "default_code" in self.env["product.template"]._fields else False,
            "barcode": values.get("barcode"),
            "weight": values.get("weight") or 0.0,
            "active": values.get("active") if "active" in values else True,
            "manufacturer_sku": values.get("manufacturer_sku"),
            "jtl_parent_sku": values.get("parent_sku") or sku,
            "jtl_seo_path": values.get("seo_path"),
            "jtl_taric_code": values.get("taric_code"),
            "jtl_short_description": values.get("short_description"),
            "jtl_description_html": values.get("description"),
            "standard_price": values.get("purchase_price") or 0.0,
        }
        category = self._find_or_create_category(values.get("category_path"), caches)
        if category:
            template_vals["categ_id"] = category.id
        manufacturer = self._find_or_create_manufacturer(values, caches)
        if manufacturer:
            template_vals["manufacturer_partner_id"] = manufacturer.id
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
        template_vals.update(
            self._extract_dynamic_fields(
                "product.template",
                template_model_data,
                excluded_fields={
                    "name",
                    "barcode",
                    "weight",
                    "active",
                    "manufacturer_sku",
                    "parent_sku",
                    "seo_path",
                    "taric_code",
                    "short_description",
                    "description",
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
        template = existing_variant.product_tmpl_id if existing_variant else template_model.search([("jtl_parent_sku", "=", sku)], limit=1)
        vals = self._prepare_template_vals(run, product_payload, caches, logs)
        if is_parent:
            vals["jtl_parent_sku"] = sku
        if template:
            template.write(vals)
            return template, False
        template = template_model.create(vals)
        if template.product_variant_id and not template.product_variant_id.default_code:
            template.product_variant_id.write({"default_code": sku, "jtl_parent_sku": vals.get("jtl_parent_sku")})
        return template, True

    def _upsert_variant_product(self, run, payload, product_payload, caches, logs, batch_number):
        parent_sku = product_payload.get("product", {}).get("parent_sku")
        parent_payload = payload.get("products", {}).get(parent_sku, {"sku": parent_sku, "product": {"name": parent_sku}})
        template, created_template = self._upsert_template_product(run, parent_payload, caches, logs, batch_number, is_parent=True)
        existing_variant = self.env["product.product"].search([("default_code", "=", product_payload.get("sku"))], limit=1)
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
                variant.write({"jtl_parent_sku": parent_sku})
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
        created_variant = False
        if variant:
            created_variant = not bool(existing_variant)
            variant_vals = {
                "default_code": product_payload.get("sku"),
                "barcode": product_payload.get("product", {}).get("barcode"),
                "weight": product_payload.get("product", {}).get("weight") or 0.0,
                "jtl_parent_sku": parent_sku,
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
        template.write(self._prepare_template_vals(run, product_payload, caches, logs))
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
            attr_value = self._find_or_create_attribute_value(attribute, value, caches)
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

    def _find_or_create_manufacturer(self, values, caches):
        normalized = self.env["jtl.import.normalizer"].normalized_name(values.get("manufacturer_name"))
        email = (values.get("manufacturer_email") or "").strip().lower()
        website = (values.get("manufacturer_website") or "").strip().lower()
        external_id = values.get("manufacturer_external_id")
        for key in (
            ("external_id", external_id),
            ("email", email),
            ("website", website),
            ("name", normalized),
        ):
            if key[1] and caches["manufacturer"].get(key):
                return caches["manufacturer"][key]
        if not normalized:
            return False
        domain = [("is_company", "=", True), ("jtl_normalized_name", "=", normalized)]
        if external_id:
            partner = self.env["res.partner"].search([("jtl_manufacturer_external_id", "=", external_id)], limit=1)
            if partner:
                caches["manufacturer"][("external_id", external_id)] = partner
                return partner
        partner = self.env["res.partner"].search(domain, limit=1)
        if not partner and email:
            partner = self.env["res.partner"].search([("email", "=ilike", email), ("is_company", "=", True)], limit=1)
        if not partner and website:
            partner = self.env["res.partner"].search([("website", "=ilike", website), ("is_company", "=", True)], limit=1)
        if not partner:
            tag = self.env["res.partner.category"].search([("is_jtl_manufacturer_tag", "=", True)], limit=1)
            if not tag:
                tag = self.env["res.partner.category"].create({"name": "Manufacturer", "is_jtl_manufacturer_tag": True})
            partner_vals = {
                "name": values.get("manufacturer_name"),
                "is_company": True,
                "email": values.get("manufacturer_email"),
                "website": values.get("manufacturer_website"),
                "is_jtl_manufacturer": True,
                "jtl_manufacturer_external_id": external_id,
                "category_id": [(4, tag.id)],
            }
            partner_vals.update(
                self._extract_dynamic_fields(
                    "res.partner",
                    values,
                    excluded_fields={"manufacturer_name", "manufacturer_email", "manufacturer_website", "manufacturer_external_id"},
                )
            )
            partner = self.env["res.partner"].create(partner_vals)
        else:
            write_vals = {"is_jtl_manufacturer": True}
            if external_id and not partner.jtl_manufacturer_external_id:
                write_vals["jtl_manufacturer_external_id"] = external_id
            if write_vals:
                partner.write(write_vals)
        caches["manufacturer"][("name", normalized)] = partner
        if email:
            caches["manufacturer"][("email", email)] = partner
        if website:
            caches["manufacturer"][("website", website)] = partner
        if external_id:
            caches["manufacturer"][("external_id", external_id)] = partner
        return partner

    def _find_or_create_category(self, category_path, caches):
        if not category_path:
            return False
        path = str(category_path).replace(">", "/")
        cache_key = " / ".join([item.strip() for item in path.split("/") if item.strip()]).lower()
        if caches["category"].get(cache_key):
            return caches["category"][cache_key]
        parent = False
        full_path_parts = []
        for part in [item.strip() for item in path.split("/") if item.strip()]:
            full_path_parts.append(part)
            part_key = " / ".join(full_path_parts).lower()
            category = caches["category"].get(part_key)
            if not category:
                domain = [("name", "=", part), ("parent_id", "=", parent.id if parent else False)]
                category = self.env["product.category"].search(domain, limit=1)
                if not category:
                    category = self.env["product.category"].create({"name": part, "parent_id": parent.id if parent else False})
                caches["category"][part_key] = category
            parent = category
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
                "description": "jtl_description_html",
                "short_description": "jtl_short_description",
                "meta_title": "jtl_meta_title",
                "meta_description": "jtl_meta_description",
            }.get(field_name)
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
                "jtl_is_default_supplier": supplier_data.get("default_supplier"),
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
                logs.append({"article_number": template.jtl_parent_sku, "field_name": "image_url", "level": "warning", "message": _("Image download failed for %s: %s") % (source_url, exc)})
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
                        "article_number": template.jtl_parent_sku,
                        "field_name": "image_url",
                        "level": "warning",
                        "message": _("Gallery image import skipped because product.image is unavailable in this Odoo instance."),
                    }
                )
                return

    def _process_stock(self, template, stock_rows, caches, logs, sku):
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
            logs.append({"article_number": template.jtl_parent_sku, "field_name": "website", "level": "warning", "message": _("Website fields are unavailable because website_sale is not installed.")})
