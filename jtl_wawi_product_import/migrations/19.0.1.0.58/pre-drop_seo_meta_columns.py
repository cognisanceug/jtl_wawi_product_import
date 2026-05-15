"""Pre-migration for 19.0.1.0.58.

In 19.0.1.0.55 the custom ``seo_path``, ``meta_title`` and ``meta_description``
columns were removed from ``product.template`` — they duplicated standard
Odoo ``website_meta_*`` fields and the form-view entries showed up empty.

On upgraded databases the orphaned DB columns and ``ir_model_fields`` rows
survive a vanilla module upgrade, which makes the web client fail with
``Invalid field 'seo_path' on 'product.template'`` whenever it requests
those names through a stored view or saved filter. This pre-migration
drops both defensively so the upgrade finishes cleanly.

``DROP COLUMN IF EXISTS`` and ``DELETE ... WHERE`` are no-ops on databases
that were already clean, so it is safe to run on any environment.
"""

import logging

_logger = logging.getLogger(__name__)

_REMOVED_FIELDS = ("seo_path", "meta_title", "meta_description")


def migrate(cr, version):
    if not version:
        # Fresh install — nothing to clean up.
        return
    for column in _REMOVED_FIELDS:
        cr.execute(
            'ALTER TABLE "product_template" DROP COLUMN IF EXISTS "%s"' % column
        )
        _logger.info(
            "jtl_wawi_product_import: dropped product_template.%s "
            "(field removed in 19.0.1.0.55)",
            column,
        )
    cr.execute(
        """
        DELETE FROM ir_model_fields
        WHERE model = 'product.template'
          AND name IN %s
        """,
        (_REMOVED_FIELDS,),
    )
    _logger.info(
        "jtl_wawi_product_import: removed orphan ir_model_fields entries for %s",
        ", ".join(_REMOVED_FIELDS),
    )
