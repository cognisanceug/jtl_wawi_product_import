"""Pre-migration for 19.0.1.0.6.

Earlier development versions of this module declared three columns on
``res.partner`` (``jtl_url_path``, ``jtl_meta_title``, ``jtl_image_url``).
That registry change collided with Odoo's `button_install` handler, which
reads ``res.partner`` *before* migrations run, so a half-installed state
(registry knows the field, DB doesn't have the column) was unrecoverable
without manual SQL.

We removed those columns from ``res.partner`` entirely. To clean up
databases that picked up the half-installed state — i.e. either the
ALTER TABLE ran successfully or the columns are still missing — this
pre-migration drops them defensively. ``DROP COLUMN IF EXISTS`` is a
no-op when the column was never created, so it is safe to run on any
database.
"""

import logging

_logger = logging.getLogger(__name__)

_LEGACY_COLUMNS = (
    "jtl_url_path",
    "jtl_meta_title",
    "jtl_image_url",
)


def migrate(cr, version):
    if not version:
        # Fresh install — nothing to clean up.
        return
    for column in _LEGACY_COLUMNS:
        cr.execute(
            'ALTER TABLE "res_partner" DROP COLUMN IF EXISTS "%s"' % column
        )
        _logger.info(
            "jtl_wawi_product_import: dropped legacy res_partner.%s "
            "(field removed in 19.0.1.0.6)",
            column,
        )
