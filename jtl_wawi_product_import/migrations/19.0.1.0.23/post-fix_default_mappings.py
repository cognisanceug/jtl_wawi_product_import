"""Post-migration for 19.0.1.0.16.

Defensive cleanup for jtl.import.mapping default records that may still
carry the pre-v19.0.1.0.15 target_field values. Earlier versions
shipped the data block with noupdate='1', so the canonical fields
were not refreshed when the plugin was upgraded.

For each XML id whose default target_field has been changed, this
script forces the DB record to the new value so the wizard preselects
the correct Odoo field on the next run. It is safe to run multiple
times.
"""

import logging

_logger = logging.getLogger(__name__)

# (xml_id, target_model, target_field) tuples — the canonical state
# we want the records to be in after this migration runs.
_FIXES = [
    ("jtl_wawi_product_import.jtl_mapping_taric_code", "product.template", "hs_code"),
    ("jtl_wawi_product_import.jtl_mapping_article_master_gross_price_alt", "product.template", "list_price"),
    ("jtl_wawi_product_import.jtl_mapping_article_master_supplier_delay_alt", "product.template", "sale_delay"),
    ("jtl_wawi_product_import.jtl_mapping_manufacturer_name", "product.template", "brand_name"),
    ("jtl_wawi_product_import.jtl_mapping_tax_rate", "account.tax", "amount"),
    ("jtl_wawi_product_import.jtl_mapping_article_master_tax_rate_alt", "account.tax", "amount"),
]


def migrate(cr, version):
    if not version:
        return
    env_data = cr.env if hasattr(cr, "env") else None
    # Use raw SQL because we don't have an env handle in raw migration
    # signature; ir_model_data points each xml_id at the underlying row.
    for xml_id, target_model, target_field in _FIXES:
        module, name = xml_id.split(".")
        cr.execute(
            """
            SELECT res_id
              FROM ir_model_data
             WHERE module = %s AND name = %s AND model = 'jtl.import.mapping'
             LIMIT 1
            """,
            (module, name),
        )
        row = cr.fetchone()
        if not row:
            _logger.info("jtl_wawi_product_import: %s not found, skipping fix", xml_id)
            continue
        res_id = row[0]
        cr.execute(
            """
            UPDATE jtl_import_mapping
               SET target_model = %s,
                   target_field = %s
             WHERE id = %s
            """,
            (target_model, target_field, res_id),
        )
        _logger.info(
            "jtl_wawi_product_import: forced %s -> %s.%s",
            xml_id, target_model, target_field,
        )
