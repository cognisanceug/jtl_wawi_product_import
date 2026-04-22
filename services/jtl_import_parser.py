import base64
import csv
import io
from collections import defaultdict

from odoo import _, models


class JtlImportParser(models.AbstractModel):
    _name = "jtl.import.parser"
    _description = "JTL CSV Parser"

    def _decode_file(self, datas):
        raw = base64.b64decode(datas or b"")
        last_error = False
        for encoding in ("utf-8-sig", "cp1252", "latin1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        raise ValueError(_("Unable to decode CSV file: %s") % last_error)

    def _get_mapping_value(self, mapping, key, default=False):
        if isinstance(mapping, dict):
            return mapping.get(key, default)
        return getattr(mapping, key, default)

    def extract_headers(self, datas):
        text = self._decode_file(datas)
        stream = io.StringIO(text, newline=None)
        reader = csv.DictReader(stream, delimiter=";")
        if not reader.fieldnames:
            raise ValueError(_("The CSV file does not contain a header row."))
        return reader.fieldnames

    def _prepare_row_payload(self, row, mappings):
        normalizer = self.env["jtl.import.normalizer"]
        payload = defaultdict(dict)
        for mapping in mappings:
            source_column = self._get_mapping_value(mapping, "source_column")
            target_model = self._get_mapping_value(mapping, "target_model")
            target_field = self._get_mapping_value(mapping, "target_field")
            language_code = self._get_mapping_value(mapping, "language_code")
            default_value = self._get_mapping_value(mapping, "default_value")
            transform_logic = self._get_mapping_value(mapping, "transform_logic", "text")
            raw_value = row.get(source_column)
            value = normalizer.apply_transform(raw_value if raw_value not in (None, "") else default_value, transform_logic)
            if language_code:
                payload[target_model].setdefault(target_field, {})[language_code] = value
            else:
                payload[target_model][target_field] = value
        return payload

    def parse_import_file(self, run, datas, filename, mapping_specs=None):
        if mapping_specs is not None:
            mappings = sorted(mapping_specs, key=lambda item: item.get("sequence", 0))
        else:
            mapping_model = self.env["jtl.import.mapping"]
            mappings = mapping_model.search([("active", "=", True)], order="sequence, id")
        if not mappings:
            raise ValueError(_("No active import mappings are configured."))

        text = self._decode_file(datas)
        stream = io.StringIO(text, newline=None)
        reader = csv.DictReader(stream, delimiter=";")
        if not reader.fieldnames:
            raise ValueError(_("The CSV file does not contain a header row."))
        header_set = set(reader.fieldnames)
        missing_required = [
            self._get_mapping_value(mapping, "source_column")
            for mapping in mappings
            if self._get_mapping_value(mapping, "required") and self._get_mapping_value(mapping, "source_column") not in header_set
        ]
        if missing_required:
            raise ValueError(
                _("Missing required CSV columns: %s")
                % ", ".join(sorted(missing_required))
            )

        grouped_rows = {}
        product_sequence = []
        warnings = []

        for row_number, row in enumerate(reader, start=2):
            if not row:
                continue
            prepared = self._prepare_row_payload(row, mappings)
            sku = prepared.get("product.product", {}).get("default_code") or row.get("Artikelnummer") or row.get("article_number")
            sku = self.env["jtl.import.normalizer"].normalize_text(sku)
            if not sku:
                warnings.append(
                    {
                        "level": "error",
                        "row_number": row_number,
                        "field_name": "Artikelnummer",
                        "message": _("Row skipped because Artikelnummer is missing."),
                    }
                )
                continue

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
                }
                product_sequence.append(sku)

            record = grouped_rows[sku]
            record["rows"].append(dict(row))
            record["row_numbers"].append(row_number)
            product_data = prepared.get("product.template", {})
            variant_data = prepared.get("product.product", {})
            partner_data = prepared.get("res.partner", {})
            supplier_data = prepared.get("product.supplierinfo", {})
            attribute_data = prepared.get("product.attribute", {})
            image_data = prepared.get("product.image", {})
            stock_data = prepared.get("stock.quant", {})
            seo_data = prepared.get("website", {})
            tax_data = prepared.get("account.tax", {})
            model_data = record["model_data"]

            for model_name, values in prepared.items():
                if model_name in ("product.supplierinfo", "product.attribute", "product.image", "stock.quant", "website"):
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

            if supplier_data:
                record["suppliers"].append(supplier_data)
            if attribute_data:
                record["attributes"].append(attribute_data)
            if image_data:
                record["images"].append(image_data)
            if stock_data:
                record["stock"].append(stock_data)
            if seo_data:
                record["seo"].update({key: value for key, value in seo_data.items() if value not in (None, False, "")})

        ordered_skus = sorted(
            product_sequence,
            key=lambda sku: (
                bool(grouped_rows[sku]["product"].get("parent_sku")),
                not bool(grouped_rows[sku]["product"].get("is_parent")),
                sku,
            ),
        )
        return {
            "filename": filename,
            "skus": ordered_skus,
            "products": grouped_rows,
            "warnings": warnings,
        }
