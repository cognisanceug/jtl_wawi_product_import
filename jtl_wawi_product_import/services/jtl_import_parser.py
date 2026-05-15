import base64
import csv
import html
import io
import re
from collections import defaultdict

from odoo import _, models

# JTL tax class names → sales tax rate in percent. The "Steuerklasse" column
# carries a class name (not a number); it is translated here and stored as
# tax_rate, which the processor resolves to the matching account.tax record.
# Keys are normalised (lowercase, umlauts spelled out) — see _normalize_tax_class.
JTL_TAX_CLASS_RATES = {
    "normaler steuersatz": 19.0,
    "ermaessigter steuersatz": 7.0,
    "steuerfrei": 0.0,
    "nicht steuerbar": 0.0,
    "ohne steuer": 0.0,
    "innergemeinschaftliche lieferung": 0.0,
}


def _normalize_tax_class(value):
    text = re.sub(r"\s+", " ", (value or "").strip().lower())
    return text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


# "Global-Englisch: <Spalte>" columns carry the English text for a field. They
# are imported as the en_US translation of the matching Odoo field. Keys are the
# base column name lowercased; values are the canonical translation keys that
# the processor's _apply_translations understands.
GLOBAL_EN_FIELD_MAP = {
    "artikelname": "name",
    "name": "name",
    "kurzbeschreibung": "short_description",
    "beschreibung": "description",
}
GLOBAL_EN_PREFIXES = ("global-englisch", "global englisch", "englisch")

# Columns prefixed "JTL-Wawi:" carry per-price-group prices. Each becomes an
# Odoo product.pricelist named after the group. "Brutto" groups are converted
# to net via the article's tax rate by the processor.
JTL_PRICE_PREFIX = "jtl-wawi:"


class JtlImportParser(models.AbstractModel):
    _name = "jtl.import.parser"
    _description = "JTL CSV Parser"

    def _make_unique_header_keys(self, headers):
        counters = defaultdict(int)
        result = []
        for header in headers:
            cleaned = (header or "").strip()
            if not cleaned:
                result.append(False)
                continue
            counters[cleaned] += 1
            key = cleaned if counters[cleaned] == 1 else "%s__%s" % (cleaned, counters[cleaned])
            result.append(key)
        return result

    def _decode_raw(self, raw):
        """Decode raw CSV bytes to text.

        UTF-16 is detected explicitly via its BOM — otherwise ``utf-8-sig``
        would "succeed" on UTF-16 input and return a string interleaved with
        NUL (0x00) characters, which PostgreSQL refuses to store
        (``ValueError: A string literal cannot contain NUL``). NUL bytes are
        stripped defensively regardless of the source encoding, since they
        never carry meaning in a CSV cell.
        """
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
                try:
                    return raw.decode(encoding).replace("\x00", "")
                except UnicodeDecodeError:
                    pass
        last_error = False
        for encoding in ("utf-8-sig", "cp1252", "latin1"):
            try:
                return raw.decode(encoding).replace("\x00", "")
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError(_("Unable to decode CSV file: %s") % last_error)

    def _sniff_csv(self, datas):
        decoded = self._decode_raw(self._get_raw_file_bytes(datas))
        sample = decoded[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";"
        return decoded, delimiter

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
        return self._decode_raw(base64.b64decode(datas or b""))

    def _get_raw_file_bytes(self, datas):
        return base64.b64decode(datas or b"")

    def _open_csv_reader(self, datas):
        decoded, delimiter = self._sniff_csv(datas)
        text_stream = io.StringIO(decoded, newline=None)
        base_reader = csv.reader(text_stream, delimiter=delimiter)
        try:
            raw_headers = next(base_reader)
        except StopIteration:
            raw_headers = []
        cleaned_headers = []
        for header in raw_headers:
            cleaned = (header or "").strip()
            cleaned_headers.append(cleaned or False)
        unique_headers = self._make_unique_header_keys(cleaned_headers)
        if not any(unique_headers):
            reader = csv.DictReader(io.StringIO("", newline=None), fieldnames=[])
            reader.fieldnames = []
            return text_stream, reader
        reader = csv.DictReader(text_stream, fieldnames=unique_headers, delimiter=delimiter)
        reader.fieldnames = unique_headers
        reader.original_fieldnames = cleaned_headers
        return text_stream, reader

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

    def analyze_file(self, datas):
        stream, reader = self._open_csv_reader(datas)
        try:
            if not reader.fieldnames:
                raise ValueError(_("The CSV file does not contain a header row."))
            columns = [
                {
                    "source_column": key,
                    "source_column_label": label or key,
                    "sample_value": False,
                    "values": [],
                }
                for key, label in zip(reader.fieldnames, getattr(reader, "original_fieldnames", reader.fieldnames))
                if key
            ]
            by_key = {column["source_column"]: column for column in columns}
            for row in reader:
                if not row:
                    continue
                for key, value in row.items():
                    column = by_key.get(key)
                    if not column:
                        continue
                    text = (value or "").strip()
                    if not text:
                        continue
                    if not column["sample_value"]:
                        column["sample_value"] = text[:255]
                    if len(column["values"]) < 25:
                        column["values"].append(text)
            result = []
            for column in columns:
                if not column["sample_value"]:
                    continue
                result.append(
                    {
                        "source_column": column["source_column"],
                        "source_column_label": column["source_column_label"],
                        "sample_value": column["sample_value"],
                        "values": column["values"],
                    }
                )
            return result
        finally:
            stream.close()

    def analyze_bundle(self, file_specs):
        analysis_by_file = {}
        for spec in file_specs or []:
            analysis_by_file[spec.get("file_key")] = self.analyze_file(spec.get("file_data"))
        return analysis_by_file

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
                "source_file_keys": [],
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

    def _parse_supplier_master_rows(self, rows, mappings):
        """Parse the JTL "Lieferantenstammdaten" file into res.partner master
        records. Suppliers are deduplicated by name within the file; the
        processor marks them with supplier_rank so they show up as vendors."""
        suppliers = {}
        warnings = []
        for row_number, row in rows:
            prepared = self._prepare_row_payload(row, mappings)
            record, warning = self._build_partner_master_record(
                row,
                prepared,
                row_number,
                "supplier",
                ("Firma", "Lieferant", "name"),
                "ref",
            )
            if warning:
                warnings.append(warning)
                continue
            supplier_name = record["name"]
            existing = suppliers.setdefault(supplier_name, record)
            if existing is not record:
                existing["rows"].append(dict(row))
                existing["model_data"]["res.partner"].update(record["model_data"]["res.partner"])
                for field_name, values in record["translations"].items():
                    existing["translations"][field_name].update(values)
        return list(suppliers.values()), warnings

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
            if file_key not in record["source_file_keys"]:
                record["source_file_keys"].append(file_key)
            record["rows"].append(dict(row))
            record["row_numbers"].append(row_number)
            if parent_sku and parent_sku != sku:
                record["product"]["parent_sku"] = parent_sku
                record["model_data"]["product.product"]["parent_sku"] = parent_sku
            if file_key == "variation_combination":
                record["product"]["is_parent"] = False
            self._merge_scalar_values(record, prepared, allowed_array_models={"product.supplierinfo", "product.attribute", "product.image", "stock.quant", "website"})

            # English overlay: "Global-Englisch: <Spalte>" columns feed the
            # en_US translation of the matching field.
            for column, raw_value in row.items():
                if not column or ":" not in column or raw_value in (None, "", False):
                    continue
                prefix_part, base_part = column.split(":", 1)
                if prefix_part.strip().lower() not in GLOBAL_EN_PREFIXES:
                    continue
                canonical = GLOBAL_EN_FIELD_MAP.get(base_part.strip().lower())
                if canonical:
                    record["translations"][canonical]["en_US"] = str(raw_value).strip()

            # "JTL-Wawi: <Preisgruppe>" columns become Odoo pricelists.
            price_groups = []
            for column, raw_value in row.items():
                if not column or raw_value in (None, "", False):
                    continue
                col_str = str(column)
                if not col_str.lower().startswith(JTL_PRICE_PREFIX):
                    continue
                group_name = col_str.split(":", 1)[1].strip()
                if not group_name:
                    continue
                price = self.env["jtl.import.normalizer"].normalize_decimal(raw_value)
                if price in (None, False) or price == 0:
                    continue
                price_groups.append({
                    "name": group_name,
                    "price": price,
                    "is_gross": "brutto" in group_name.lower(),
                })
            if price_groups:
                record["product"]["pricelist_prices"] = price_groups

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
            else:
                stock_quantity = self._get_row_value(row, "Auf Lager", "Auflager", "Bestand")
                # For the article master, stock is only taken when JTL flags the
                # article as stock-managed. Other files (e.g. Lieferantenartikel)
                # have no such flag — there the quantity column is taken as-is.
                if file_key == "article_master" and not self.env["jtl.import.normalizer"].normalize_boolean(
                    self._get_row_value(row, "Bestandsführung aktiv")
                ):
                    stock_quantity = False
                if stock_quantity not in (None, False, ""):
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
                    record["seo"].setdefault("meta_title", self._get_row_value(row, "Titel-Tag"))
                    record["seo"]["public_category_name"] = seo_path
                seo_path_en = self._get_row_value(row, "URL-Pfad Englisch")
                if seo_path_en:
                    record["seo"].setdefault("public_category_name_translations", {})["en_US"] = seo_path_en
            # "Warengruppe" is a flat single-level category. Used as the category
            # path when no multi-level path was built from Kategorie Ebene 1–4.
            if "category_path" not in record["product"]:
                warengruppe = self._get_row_value(row, "Warengruppe")
                if warengruppe:
                    record["product"]["category_path"] = str(warengruppe).strip()
            if file_key == "article_master":
                tax_class = self._get_row_value(row, "Steuerklasse", "Steuerschlüssel")
                if tax_class:
                    rate = JTL_TAX_CLASS_RATES.get(_normalize_tax_class(tax_class))
                    if rate is not None:
                        record["product"].setdefault("tax_rate", rate)
                # "USt. in %" belongs to the embedded supplier block — it is the
                # purchase tax rate, resolved to supplier_taxes_id on the product.
                purchase_ust = self._get_row_value(row, "USt. in %")
                if purchase_ust not in (None, False, ""):
                    purchase_rate = self.env["jtl.import.normalizer"].normalize_decimal(
                        str(purchase_ust).replace("%", "").strip()
                    )
                    if purchase_rate not in (None, False):
                        record["product"].setdefault("purchase_tax_rate", purchase_rate)
            # The supplier block also appears embedded in the article master
            # file (Lieferant / Artikelnummer (Lieferant) / Netto-EK / …).
            if file_key in ("supplierinfo", "article_master") and not supplier_data:
                normalizer = self.env["jtl.import.normalizer"]
                supplier_name = self._get_row_value(row, "Lieferant")
                if supplier_name:
                    record["suppliers"].append(
                        {
                            "supplier_name": supplier_name,
                            "supplier_product_number": self._get_row_value(row, "Artikelnummer (Lieferant)"),
                            "product_name": self._get_row_value(row, "Artikelname (Lieferant)"),
                            "purchase_price": normalizer.normalize_decimal(self._get_row_value(row, "Netto-EK")),
                            "minimum_quantity": normalizer.normalize_decimal(self._get_row_value(row, "Mindestabnahme Lieferant")),
                            "lead_time": normalizer.normalize_decimal(self._get_row_value(row, "Lieferzeit in Tagen (Lieferant)")),
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
        supplier_master_rows = []
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
            elif file_key == "supplier_master":
                parsed_suppliers, file_warnings = self._parse_supplier_master_rows(rows, file_mappings)
                supplier_master_rows.extend(parsed_suppliers)
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
        # Deduplicated category descriptors from the category file. These let
        # the processor build the category hierarchy in a single pass instead
        # of walking every product row.
        category_entries = {}
        for sku, record in grouped_rows.items():
            if "category" not in (record.get("source_file_keys") or []):
                continue
            category_path = (record.get("product") or {}).get("category_path")
            if not category_path or category_path in category_entries:
                continue
            category_entries[category_path] = {
                "category_path": category_path,
                "seo": dict(record.get("seo") or {}),
            }
        return {
            "filename": ", ".join(processed_file_names),
            "skus": ordered_skus,
            "products": grouped_rows,
            "categories": list(category_entries.values()),
            "manufacturers": manufacturer_rows,
            "eu_responsibles": eu_responsible_rows,
            "suppliers_master": supplier_master_rows,
            "warnings": warnings,
        }
