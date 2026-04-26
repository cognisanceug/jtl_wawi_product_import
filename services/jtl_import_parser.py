import base64
import csv
import html
import io
import re
from collections import defaultdict

from odoo import _, models


class JtlImportParser(models.AbstractModel):
    _name = "jtl.import.parser"
    _description = "JTL CSV Parser"

    def _html_to_text_lines(self, value):
        text = html.unescape(value or "")
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p\s*>", "\n", text)
        text = re.sub(r"(?i)</div\s*>", "\n", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = text.replace("\xa0", " ")
        lines = []
        for line in text.splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip()
            if cleaned:
                lines.append(cleaned)
        return lines

    def _extract_partner_data_from_description(self, description_html):
        lines = self._html_to_text_lines(description_html)
        if not lines:
            return {}
        ignored_markers = (
            "hersteller/ kontaktinformationen",
            "manufacturer/ contact information",
            "kontaktinformationen",
            "contact information",
        )
        content_lines = [
            line for line in lines if not any(marker in line.lower() for marker in ignored_markers)
        ]
        if not content_lines:
            return {}

        extracted = {}
        address_lines = []
        postal_line_index = False
        for index, line in enumerate(content_lines):
            lower_line = line.lower()
            if "mailto:" in lower_line:
                continue
            email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", line)
            if email_match:
                extracted.setdefault("email", email_match.group(0))
                continue
            if lower_line.startswith(("telefon:", "telefon ", "phone:", "phone ", "tel:", "tel ")):
                extracted.setdefault("phone", line.split(":", 1)[1].strip() if ":" in line else line)
                continue
            if lower_line.startswith(("telefax:", "fax:", "fax ")):
                extracted.setdefault("fax", line.split(":", 1)[1].strip() if ":" in line else line)
                continue
            if re.match(r"^\d{4,6}\s+.+", line):
                postal_line_index = index
                zip_match = re.match(r"^(\d{4,6})\s+(.+)$", line)
                if zip_match:
                    extracted.setdefault("zip", zip_match.group(1))
                    extracted.setdefault("city", zip_match.group(2).strip())
                continue
            address_lines.append((index, line))

        if postal_line_index is not False:
            street_candidates = [line for index, line in address_lines if index < postal_line_index]
            if street_candidates:
                extracted.setdefault("street", street_candidates[-1])
                if len(street_candidates) > 1:
                    extracted.setdefault("street2", ", ".join(street_candidates[:-1]))

        return extracted

    def _decode_file(self, datas):
        raw = base64.b64decode(datas or b"")
        last_error = False
        for encoding in ("utf-8-sig", "cp1252", "latin1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError(_("Unable to decode CSV file: %s") % last_error)

    def _get_raw_file_bytes(self, datas):
        return base64.b64decode(datas or b"")

    def _open_csv_reader(self, datas):
        raw = self._get_raw_file_bytes(datas)
        last_error = False
        for encoding in ("utf-8-sig", "cp1252", "latin1"):
            try:
                decoded = raw.decode(encoding)
                text_stream = io.StringIO(decoded, newline=None)
                sample = decoded[:8192]
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
                    delimiter = dialect.delimiter
                except csv.Error:
                    delimiter = ";"
                reader = csv.DictReader(text_stream, delimiter=delimiter)
                _ = reader.fieldnames
                return text_stream, reader
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError(_("Unable to decode CSV file: %s") % last_error)

    def _get_mapping_value(self, mapping, key, default=False):
        if isinstance(mapping, dict):
            return mapping.get(key, default)
        return getattr(mapping, key, default)

    def extract_headers(self, datas):
        stream, reader = self._open_csv_reader(datas)
        try:
            if not reader.fieldnames:
                raise ValueError(_("The CSV file does not contain a header row."))
            headers = []
            for fieldname in reader.fieldnames:
                if fieldname is None:
                    continue
                cleaned = fieldname.strip()
                if cleaned:
                    headers.append(cleaned)
            return headers
        finally:
            stream.close()

    def extract_headers_bundle(self, file_specs):
        headers_by_file = {}
        for spec in file_specs or []:
            headers_by_file[spec.get("file_key")] = self.extract_headers(spec.get("file_data"))
        return headers_by_file

    def _prepare_row_payload(self, row, mappings):
        normalizer = self.env["jtl.import.normalizer"]
        payload = defaultdict(dict)
        for mapping in mappings:
            source_column = self._get_mapping_value(mapping, "source_column")
            target_model = self._get_mapping_value(mapping, "target_model")
            target_field = self._get_mapping_value(mapping, "target_field")
            language_code = self._get_mapping_value(mapping, "language_code")
            if not language_code:
                language = self._get_mapping_value(mapping, "language_id")
                language_code = language.code if language else False
            default_value = self._get_mapping_value(mapping, "default_value")
            transform_logic = self._get_mapping_value(mapping, "transform_logic", "text")
            raw_value = row.get(source_column)
            value = normalizer.apply_transform(raw_value if raw_value not in (None, "") else default_value, transform_logic)
            if language_code:
                payload[target_model].setdefault(target_field, {})[language_code] = value
            else:
                payload[target_model][target_field] = value
        return payload

    def _get_mapping_specs_for_file(self, mappings, file_key):
        return [
            mapping
            for mapping in mappings
            if self._get_mapping_value(mapping, "source_file_key", "article_master") == file_key
        ]

    def _get_row_value(self, row, *keys):
        for key in keys:
            value = row.get(key)
            if value not in (None, False, ""):
                return value
        return False

    def _ensure_group_record(self, grouped_rows, product_sequence, sku):
        if sku not in grouped_rows:
            grouped_rows[sku] = {
                "sku": sku,
                "rows": [],
                "row_numbers": [],
                "product": {},
                "model_data": defaultdict(dict),
                "translations": defaultdict(dict),
                "suppliers": [],
                "images": [],
                "attributes": [],
                "stock": [],
                "seo": {},
                "bom": [],
            }
            product_sequence.append(sku)
        return grouped_rows[sku]

    def _build_partner_master_record(self, row, prepared, row_number, role_prefix, default_name_keys, default_id_field):
        partner_data = prepared.setdefault("res.partner", {})
        partner_name = partner_data.get("name")
        if not partner_name:
            partner_name = self._get_row_value(row, *default_name_keys)
        partner_name = self.env["jtl.import.normalizer"].normalize_text(partner_name)
        if not partner_name:
            return False, {
                "level": "warning",
                "row_number": row_number,
                "field_name": default_name_keys[0],
                "message": _("Partner row skipped because no name could be determined."),
            }
        record = {
            "name": partner_name,
            "model_data": defaultdict(dict),
            "translations": defaultdict(dict),
            "rows": [dict(row)],
            "role_prefix": role_prefix,
        }
        for model_name, values in prepared.items():
            if model_name != "res.partner":
                continue
            for key, value in values.items():
                if isinstance(value, dict):
                    record["translations"][key].update(
                        {lang: val for lang, val in value.items() if val not in (None, False, "")}
                    )
                elif value not in (None, False, ""):
                    record["model_data"]["res.partner"][key] = value
        if default_id_field and row.get(default_id_field) and not record["model_data"]["res.partner"].get(default_id_field):
            record["model_data"]["res.partner"][default_id_field] = row.get(default_id_field)
        return record, False

    def _merge_scalar_values(self, record, prepared, allowed_array_models=None):
        allowed_array_models = set(allowed_array_models or [])
        product_data = prepared.get("product.template", {})
        variant_data = prepared.get("product.product", {})
        partner_data = prepared.get("res.partner", {})
        tax_data = prepared.get("account.tax", {})
        model_data = record["model_data"]
        for model_name, values in prepared.items():
            if model_name in allowed_array_models:
                continue
            for key, value in values.items():
                if not isinstance(value, dict):
                    model_data[model_name][key] = value

        for key, value in {**product_data, **variant_data, **partner_data, **tax_data}.items():
            if isinstance(value, dict):
                cleaned_values = {lang: val for lang, val in value.items() if val not in (None, False, "")}
                record["translations"][key].update(cleaned_values)
                if cleaned_values and key not in record["product"]:
                    default_value = (
                        cleaned_values.get("de_DE")
                        or cleaned_values.get("de")
                        or cleaned_values.get("en_US")
                        or cleaned_values.get("en")
                        or next(iter(cleaned_values.values()))
                    )
                    record["product"][key] = default_value
            elif value not in (None, False, ""):
                record["product"][key] = value

    def _parse_manufacturer_rows(self, rows, mappings):
        manufacturers = {}
        warnings = []
        for row_number, row in rows:
            prepared = self._prepare_row_payload(row, mappings)
            partner_data = prepared.setdefault("res.partner", {})
            description_value = self._get_row_value(row, "Beschreibung", "Description")
            extracted_contact_data = self._extract_partner_data_from_description(description_value)
            if extracted_contact_data:
                for field_name, value in extracted_contact_data.items():
                    if partner_data.get(field_name) in (None, False, "") and value not in (None, False, ""):
                        partner_data[field_name] = value
            record, warning = self._build_partner_master_record(
                row,
                prepared,
                row_number,
                "manufacturer",
                ("Hersteller", "manufacturer", "name"),
                "manufacturer_external_id",
            )
            if warning:
                warnings.append(warning)
                continue
            manufacturer_name = record["name"]
            existing = manufacturers.setdefault(manufacturer_name, record)
            if existing is not record:
                existing["rows"].append(dict(row))
                existing["model_data"]["res.partner"].update(record["model_data"]["res.partner"])
                for field_name, values in record["translations"].items():
                    existing["translations"][field_name].update(values)
        return list(manufacturers.values()), warnings

    def _parse_eu_responsible_rows(self, rows, mappings):
        representatives = {}
        warnings = []
        for row_number, row in rows:
            prepared = self._prepare_row_payload(row, mappings)
            record, warning = self._build_partner_master_record(
                row,
                prepared,
                row_number,
                "eu_responsible",
                ("EU RP", "EU Representative", "name"),
                "eu_responsible_external_id",
            )
            if warning:
                warnings.append(warning)
                continue
            partner_name = record["name"]
            existing = representatives.setdefault(partner_name, record)
            if existing is not record:
                existing["rows"].append(dict(row))
                existing["model_data"]["res.partner"].update(record["model_data"]["res.partner"])
                for field_name, values in record["translations"].items():
                    existing["translations"][field_name].update(values)
        return list(representatives.values()), warnings

    def _parse_product_rows(self, file_key, rows, mappings, grouped_rows, product_sequence):
        warnings = []
        for row_number, row in rows:
            prepared = self._prepare_row_payload(row, mappings)
            if file_key == "variation_combination":
                sku = (
                    prepared.get("product.product", {}).get("default_code")
                    or self._get_row_value(row, "Kind Artikelnummer", "child_sku", "Artikelnummer")
                )
                parent_sku = (
                    prepared.get("product.product", {}).get("parent_sku")
                    or self._get_row_value(row, "Artikelnummer", "parent_sku")
                )
            else:
                sku = (
                    prepared.get("product.product", {}).get("default_code")
                    or self._get_row_value(row, "Artikelnummer", "article_number")
                )
                parent_sku = prepared.get("product.product", {}).get("parent_sku") or self._get_row_value(
                    row, "Vaterartikelnummer", "Identifizierungsspalte Vaterartikel"
                )
            sku = self.env["jtl.import.normalizer"].normalize_text(sku)
            parent_sku = self.env["jtl.import.normalizer"].normalize_text(parent_sku)
            if not sku:
                warnings.append(
                    {
                        "level": "error",
                        "row_number": row_number,
                        "field_name": "Artikelnummer",
                        "message": _("Row skipped because no article number could be determined for file %s.") % file_key,
                    }
                )
                continue

            record = self._ensure_group_record(grouped_rows, product_sequence, sku)
            record["rows"].append(dict(row))
            record["row_numbers"].append(row_number)
            if parent_sku and parent_sku != sku:
                record["product"]["parent_sku"] = parent_sku
                record["model_data"]["product.product"]["parent_sku"] = parent_sku
            if file_key == "variation_combination":
                record["product"]["is_parent"] = False
            self._merge_scalar_values(record, prepared, allowed_array_models={"product.supplierinfo", "product.attribute", "product.image", "stock.quant", "website"})

            supplier_data = prepared.get("product.supplierinfo", {})
            attribute_data = prepared.get("product.attribute", {})
            image_data = prepared.get("product.image", {})
            stock_data = prepared.get("stock.quant", {})
            seo_data = prepared.get("website", {})

            if supplier_data:
                record["suppliers"].append(supplier_data)
            if attribute_data:
                record["attributes"].append(attribute_data)
            if image_data:
                record["images"].append(image_data)
            if stock_data:
                record["stock"].append(stock_data)
            elif file_key == "article_master":
                stock_enabled = self.env["jtl.import.normalizer"].normalize_boolean(
                    self._get_row_value(row, "Bestandsführung aktiv")
                )
                stock_quantity = self._get_row_value(row, "Auf Lager", "Auflager", "Bestand")
                if stock_enabled and stock_quantity not in (None, False, ""):
                    record["stock"].append(
                        {
                            "quantity": self.env["jtl.import.normalizer"].normalize_decimal(stock_quantity),
                        }
                    )
            if seo_data:
                record["seo"].update({key: value for key, value in seo_data.items() if value not in (None, False, "")})

            if file_key == "bom":
                bom_component_sku = self._get_row_value(row, "Artikelnummer Stücklistenkomponente", "component_sku")
                bom_quantity = prepared.get("product.supplierinfo", {}).get("min_qty") or self._get_row_value(row, "Menge", "quantity")
                if bom_component_sku:
                    record["bom"].append(
                        {
                            "component_sku": self.env["jtl.import.normalizer"].normalize_text(bom_component_sku),
                            "quantity": self.env["jtl.import.normalizer"].normalize_decimal(bom_quantity),
                            "component_name": self._get_row_value(row, "Name Stücklistenkomponente", "component_name"),
                        }
                    )

            if file_key == "variation_definition":
                variation_name = self._get_row_value(row, "Variation")
                variation_value = self._get_row_value(row, "Variationswert")
                if variation_name and variation_value:
                    record["attributes"].append(
                        {
                            "name": variation_name,
                            "value": variation_value,
                            "is_variant": True,
                        }
                    )
            if file_key == "variation_combination":
                for index in range(1, 5):
                    variation_name = self._get_row_value(row, "Variationsname %s" % index)
                    variation_value = self._get_row_value(row, "Variationswertname %s" % index)
                    if variation_name and variation_value:
                        record["attributes"].append(
                            {
                                "name": variation_name,
                                "value": variation_value,
                                "is_variant": True,
                            }
                        )
            if file_key in ("article_master", "category"):
                category_levels = [
                    self._get_row_value(row, "Kategorie Ebene 1"),
                    self._get_row_value(row, "Kategorie Ebene 2"),
                    self._get_row_value(row, "Kategorie Ebene 3"),
                    self._get_row_value(row, "Kategorie Ebene 4"),
                ]
                category_path = " / ".join([level.strip() for level in category_levels if level and str(level).strip()])
                if category_path:
                    record["product"]["category_path"] = category_path
                seo_path = self._get_row_value(row, "URL-Pfad")
                if seo_path:
                    record["product"]["seo_path"] = seo_path
                    record["seo"].setdefault("meta_title", self._get_row_value(row, "Titel-Tag"))
                    record["seo"]["public_category_name"] = seo_path
                seo_path_en = self._get_row_value(row, "URL-Pfad Englisch")
                if seo_path_en:
                    record["seo"].setdefault("public_category_name_translations", {})["en_US"] = seo_path_en
            if file_key == "supplierinfo" and not supplier_data:
                supplier_name = self._get_row_value(row, "Lieferant")
                if supplier_name:
                    record["suppliers"].append(
                        {
                            "supplier_name": supplier_name,
                            "supplier_product_number": self._get_row_value(row, "Artikelnummer (Lieferant)"),
                            "purchase_price": self.env["jtl.import.normalizer"].normalize_decimal(self._get_row_value(row, "Netto-EK")),
                            "minimum_quantity": self.env["jtl.import.normalizer"].normalize_decimal(self._get_row_value(row, "Mindestabnahme Lieferant")),
                            "default_supplier": self._get_row_value(row, "Ist Standardlieferant") in ("1", "Y", "y", "true", "True"),
                        }
                    )
        return warnings

    def parse_import_file(self, run, datas, filename, mapping_specs=None):
        return self.parse_import_bundle(
            run,
            [{"file_key": "article_master", "file_name": filename, "file_data": datas}],
            mapping_specs=mapping_specs,
        )

    def parse_import_bundle(self, run, file_specs, mapping_specs=None):
        if mapping_specs is not None:
            mappings = sorted(mapping_specs, key=lambda item: item.get("sequence", 0))
        else:
            mapping_model = self.env["jtl.import.mapping"]
            mappings = mapping_model.search([("active", "=", True)], order="sequence, id")
        if not mappings:
            raise ValueError(_("No active import mappings are configured."))

        grouped_rows = {}
        product_sequence = []
        manufacturer_rows = []
        eu_responsible_rows = []
        warnings = []
        processed_file_names = []

        for spec in file_specs or []:
            file_key = spec.get("file_key") or "article_master"
            file_name = spec.get("file_name") or file_key
            file_mappings = self._get_mapping_specs_for_file(mappings, file_key)
            if not file_mappings:
                continue
            stream, reader = self._open_csv_reader(spec.get("file_data"))
            try:
                if not reader.fieldnames:
                    raise ValueError(_("The CSV file %s does not contain a header row.") % file_name)
                header_set = set(reader.fieldnames)
                missing_required = [
                    self._get_mapping_value(mapping, "source_column")
                    for mapping in file_mappings
                    if self._get_mapping_value(mapping, "required") and self._get_mapping_value(mapping, "source_column") not in header_set
                ]
                if missing_required:
                    raise ValueError(
                        _("Missing required CSV columns in %s: %s")
                        % (file_name, ", ".join(sorted(missing_required)))
                    )
                rows = [(row_number, row) for row_number, row in enumerate(reader, start=2) if row]
            finally:
                stream.close()
            processed_file_names.append(file_name)
            if file_key == "manufacturer":
                parsed_manufacturers, file_warnings = self._parse_manufacturer_rows(rows, file_mappings)
                manufacturer_rows.extend(parsed_manufacturers)
                warnings.extend(file_warnings)
            elif file_key == "eu_representative":
                parsed_representatives, file_warnings = self._parse_eu_responsible_rows(rows, file_mappings)
                eu_responsible_rows.extend(parsed_representatives)
                warnings.extend(file_warnings)
            else:
                warnings.extend(
                    self._parse_product_rows(file_key, rows, file_mappings, grouped_rows, product_sequence)
                )

        ordered_skus = sorted(
            product_sequence,
            key=lambda sku: (
                bool(grouped_rows[sku]["product"].get("parent_sku")),
                not bool(grouped_rows[sku]["product"].get("is_parent")),
                sku,
            ),
        )
        return {
            "filename": ", ".join(processed_file_names),
            "skus": ordered_skus,
            "products": grouped_rows,
            "manufacturers": manufacturer_rows,
            "eu_responsibles": eu_responsible_rows,
            "warnings": warnings,
        }
