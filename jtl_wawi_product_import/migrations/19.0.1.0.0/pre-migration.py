import logging

_logger = logging.getLogger(__name__)


def _table_columns(cr, table_name):
    cr.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = %s
        """,
        (table_name,),
    )
    return {row[0] for row in cr.fetchall()}


def _rename_column(cr, table_name, old_name, new_name):
    columns = _table_columns(cr, table_name)
    if old_name not in columns:
        return
    if new_name in columns:
        _logger.warning(
            "Skipping rename of %s.%s to %s because the target column already exists.",
            table_name,
            old_name,
            new_name,
        )
        return
    cr.execute(f'ALTER TABLE "{table_name}" RENAME COLUMN "{old_name}" TO "{new_name}"')
    _logger.info("Renamed %s.%s to %s", table_name, old_name, new_name)


def migrate(cr, version):
    if not version:
        return

    # res.partner
    _rename_column(cr, "res_partner", "is_jtl_manufacturer", "is_manufacturer")
    _rename_column(cr, "res_partner", "is_jtl_gdpr_responsible", "is_gdpr_responsible")
    _rename_column(cr, "res_partner", "is_jtl_eu_responsible", "is_eu_responsible")
    _rename_column(cr, "res_partner", "jtl_manufacturer_external_id", "manufacturer_external_id")
    _rename_column(cr, "res_partner", "jtl_eu_responsible_external_id", "eu_responsible_external_id")
    _rename_column(cr, "res_partner", "jtl_normalized_name", "normalized_name")

    # res.partner.category
    _rename_column(cr, "res_partner_category", "is_jtl_manufacturer_tag", "is_manufacturer_tag")

    # product.template
    _rename_column(cr, "product_template", "jtl_manufacturer_partner_id", "manufacturer_partner_id")
    _rename_column(cr, "product_template", "jtl_eu_responsible_partner_id", "eu_responsible_partner_id")
    _rename_column(cr, "product_template", "jtl_gdpr_responsible_id", "gdpr_responsible_id")
    _rename_column(cr, "product_template", "jtl_brand_id", "brand_id")
    _rename_column(cr, "product_template", "jtl_parent_sku", "parent_sku")
    _rename_column(cr, "product_template", "jtl_seo_path", "seo_path")
    _rename_column(cr, "product_template", "jtl_meta_title", "meta_title")
    _rename_column(cr, "product_template", "jtl_meta_description", "meta_description")
    _rename_column(cr, "product_template", "jtl_length", "length")
    _rename_column(cr, "product_template", "jtl_width", "width")
    _rename_column(cr, "product_template", "jtl_height", "height")
    _rename_column(cr, "product_template", "jtl_volume", "volume")

    # product.product
    _rename_column(cr, "product_product", "jtl_parent_sku", "parent_sku")

    # product.supplierinfo
    _rename_column(cr, "product_supplierinfo", "jtl_is_default_supplier", "is_default_supplier")

    # product.brand
    _rename_column(cr, "product_brand", "jtl_manufacturer_id", "manufacturer_id")
    _rename_column(cr, "product_brand", "jtl_gdpr_responsible_id", "gdpr_responsible_id")
    _rename_column(cr, "product_brand", "jtl_brand_external_id", "brand_external_id")
