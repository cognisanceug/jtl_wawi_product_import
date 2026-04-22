import html
import json
import re
from decimal import Decimal, InvalidOperation

from odoo import models


class JtlImportNormalizer(models.AbstractModel):
    _name = "jtl.import.normalizer"
    _description = "JTL Import Normalizer"

    def normalize_text(self, value):
        if value in (None, False):
            return False
        text = str(value).replace("\ufeff", "").strip()
        return text or False

    def normalize_html(self, value):
        text = self.normalize_text(value)
        if not text:
            return False
        return html.unescape(text)

    def normalize_path(self, value):
        text = self.normalize_text(value)
        if not text:
            return False
        return re.sub(r"\s+", "-", text.strip("/"))

    def normalize_boolean(self, value):
        text = (self.normalize_text(value) or "").lower()
        if text in {"1", "true", "yes", "ja", "y", "x"}:
            return True
        if text in {"0", "false", "no", "nein", "n"}:
            return False
        return False

    def normalize_integer(self, value):
        text = self.normalize_text(value)
        if not text:
            return 0
        try:
            return int(float(text.replace(".", "").replace(",", ".")))
        except (TypeError, ValueError):
            return 0

    def normalize_decimal(self, value):
        text = self.normalize_text(value)
        if not text:
            return 0.0
        cleaned = text.replace(" ", "").replace("%", "")
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", ".")
        try:
            return float(Decimal(cleaned))
        except (InvalidOperation, ValueError, TypeError):
            return 0.0

    def apply_transform(self, value, transform_logic):
        if transform_logic == "decimal":
            return self.normalize_decimal(value)
        if transform_logic == "boolean":
            return self.normalize_boolean(value)
        if transform_logic == "html":
            return self.normalize_html(value)
        if transform_logic == "integer":
            return self.normalize_integer(value)
        if transform_logic == "path":
            return self.normalize_path(value)
        return self.normalize_text(value)

    def dumps_payload(self, payload):
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    def normalized_name(self, value):
        return re.sub(r"\s+", " ", (value or "").strip().lower())

