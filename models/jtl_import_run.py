import base64
import gzip
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class JtlImportRun(models.Model):
    _name = "jtl.import.run"
    _description = "JTL Import Run"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(required=True, default=lambda self: _("New"))
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("validated", "Validated"),
            ("queued", "Queued"),
            ("running", "Running"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    filename = fields.Char(required=True)
    source_attachment_id = fields.Many2one("ir.attachment", readonly=True, copy=False)
    payload_attachment_id = fields.Many2one("ir.attachment", readonly=True, copy=False)
    validation_message = fields.Text(readonly=True)
    batch_size = fields.Integer(default=200, required=True)
    current_index = fields.Integer(default=0, readonly=True)
    total_products = fields.Integer(default=0, readonly=True)
    last_batch_number = fields.Integer(default=0, readonly=True)
    import_stock = fields.Boolean(default=False)
    import_images = fields.Boolean(default=True)
    import_gallery_images = fields.Boolean(default=False)
    import_seo = fields.Boolean(default=False)
    dry_run = fields.Boolean(default=False)
    active_test = fields.Boolean(default=True)
    last_error = fields.Text(readonly=True)
    created_products = fields.Integer(default=0, readonly=True)
    updated_products = fields.Integer(default=0, readonly=True)
    created_suppliers = fields.Integer(default=0, readonly=True)
    updated_suppliers = fields.Integer(default=0, readonly=True)
    created_variants = fields.Integer(default=0, readonly=True)
    warnings_count = fields.Integer(default=0, readonly=True)
    errors_count = fields.Integer(default=0, readonly=True)
    log_ids = fields.One2many("jtl.import.log", "run_id", readonly=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)

    _sql_constraints = [
        ("jtl_import_run_batch_size_positive", "check(batch_size > 0)", "Batch size must be greater than zero."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("jtl.import.run") or _("New")
        return super().create(vals_list)

    def _store_binary_attachment(self, name, payload_bytes, mimetype):
        self.ensure_one()
        return self.env["ir.attachment"].create(
            {
                "name": name,
                "type": "binary",
                "datas": base64.b64encode(payload_bytes),
                "mimetype": mimetype,
                "res_model": self._name,
                "res_id": self.id,
            }
        )

    def set_source_file(self, filename, datas):
        self.ensure_one()
        if self.source_attachment_id:
            self.source_attachment_id.unlink()
        self.source_attachment_id = self._store_binary_attachment(filename, base64.b64decode(datas), "text/csv")
        self.filename = filename

    def set_payload(self, payload):
        self.ensure_one()
        payload_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        compressed = gzip.compress(payload_json)
        if self.payload_attachment_id:
            self.payload_attachment_id.unlink()
        self.payload_attachment_id = self._store_binary_attachment("%s.payload.json.gz" % self.filename, compressed, "application/gzip")
        self.total_products = len(payload.get("skus", []))

    def get_payload(self):
        self.ensure_one()
        if not self.payload_attachment_id:
            return {}
        datas = base64.b64decode(self.payload_attachment_id.datas or b"")
        return json.loads(gzip.decompress(datas).decode("utf-8"))

    def action_reset_to_draft(self):
        for run in self:
            run.write(
                {
                    "state": "draft",
                    "current_index": 0,
                    "last_batch_number": 0,
                    "last_error": False,
                    "created_products": 0,
                    "updated_products": 0,
                    "created_suppliers": 0,
                    "updated_suppliers": 0,
                    "created_variants": 0,
                    "warnings_count": 0,
                    "errors_count": 0,
                }
            )
            run.log_ids.unlink()

    def action_queue(self):
        for run in self:
            if run.state not in ("validated", "failed", "done"):
                raise UserError(_("Only validated, failed, or finished runs can be queued."))
            run.write({"state": "queued", "last_error": False})

    def action_process_next_batch(self):
        processor = self.env["jtl.import.processor"]
        for run in self:
            processor.process_run_batch(run)
        return True

    def action_open_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Import Logs"),
            "res_model": "jtl.import.log",
            "view_mode": "list,form",
            "domain": [("run_id", "=", self.id)],
            "context": {"default_run_id": self.id},
        }

    def _append_logs(self, log_values):
        self.ensure_one()
        if not log_values:
            return
        values = []
        for item in log_values:
            values.append(
                {
                    "run_id": self.id,
                    "batch_number": item.get("batch_number") or self.last_batch_number,
                    "row_number": item.get("row_number"),
                    "article_number": item.get("article_number"),
                    "field_name": item.get("field_name"),
                    "level": item.get("level", "info"),
                    "message": item.get("message"),
                }
            )
        self.env["jtl.import.log"].create(values)
        warning_count = len([log for log in values if log["level"] == "warning"])
        error_count = len([log for log in values if log["level"] == "error"])
        if warning_count or error_count:
            self.write(
                {
                    "warnings_count": self.warnings_count + warning_count,
                    "errors_count": self.errors_count + error_count,
                }
            )

    @api.model
    def cron_process_queued_runs(self):
        runs = self.search([("state", "in", ("queued", "running"))], order="id asc", limit=5)
        for run in runs:
            try:
                self.env["jtl.import.processor"].process_run_batch(run)
            except Exception as exc:  # pragma: no cover - cron safety
                _logger.exception("JTL import run %s failed", run.id)
                run.write({"state": "failed", "last_error": str(exc)})
