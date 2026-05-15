import base64
import gzip
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_SITEMAP_NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
}
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
# Browser-like header set used for shop search / og:image extraction. Some
# JTL/Shopware setups serve a 13 KB stub instead of the real product HTML
# when the request looks too minimal (Accept missing, only urllib UA, …).
# Sending the same Accept / Accept-Language a normal browser would send
# makes the shop return the real page in most cases.
_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Default substrings that mark a JTL-Shop placeholder image
# ("no picture available") served when the product has no real image. The
# value is shop-configurable via the `placeholder_markers` field on the
# jtl.image.sync record — JTL templates can ship different placeholder
# filenames (e.g. /gfx/keinBild.gif vs. /templates/.../no_picture.png).
_DEFAULT_PLACEHOLDER_IMAGE_MARKERS = "keinBild,no_picture,no-picture,placeholder"


def _parse_placeholder_markers(raw):
    """Split a comma/semicolon/newline-separated marker list into lowercase tokens."""
    if not raw:
        return ()
    parts = re.split(r"[,;\n]+", raw)
    return tuple(p.strip().lower() for p in parts if p.strip())


def _is_placeholder_image_url(url, markers):
    if not url or not markers:
        return False
    lowered = url.lower()
    return any(marker in lowered for marker in markers)

# Tokens JTL adds to its SEO slugs that are NOT part of the product name —
# stripped before fuzzy matching so they don't cause spurious mismatches.
# Example shop slug "schaltauge-053-fuer-bh" vs. product "BH Schaltauge 053":
# without removing "fuer", the slug tokens contain it but the name tokens
# don't, and the subset check would still hold (good) — but we filter it on
# both sides for symmetry and clarity.
_FILLER_TOKENS = frozenset({
    "fuer", "for", "with", "and", "und", "der", "die", "das",
    "von", "vom", "mit", "ohne", "in", "im", "an", "am",
})


# File extensions some shops append to product URLs (Magento, Shopware,
# legacy CMSes). Stripped before slug extraction so the matcher works
# regardless of URL layout.
_URL_EXTENSIONS = (".html", ".htm", ".php", ".asp", ".aspx")


def slugify_name(name):
    """Best-effort SEO slug for a product name.

    Optimised for German shops (JTL umlaut transliteration) but works for
    any latin-script name: characters with diacritics are NFKD-decomposed
    so e.g. „Café Niño 4" still produces the canonical „cafe-nino-4".
    """
    text = (name or "").strip()
    # Apply German umlaut transliteration before NFKD so we get „ae" / „oe"
    # rather than bare „a" / „o" (NFKD on „ä" decomposes to a + combining
    # diaeresis, which we then strip).
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
                     ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text.lower()


# Backwards-compat alias — earlier versions named this function slugify_jtl
# because it was modelled on JTL-Shop's SEO slugs. Other plugins/code may
# still import it.
slugify_jtl = slugify_name


def _url_to_slug(url):
    """Return the lowercase product slug embedded in a sitemap URL.

    Works for plain JTL-style URLs (`/Foo-Bar-123`) and for shops that use
    file extensions or query strings (`/product/foo-bar-123.html?lang=en`).
    """
    if not url:
        return ""
    parsed = urlsplit(url.strip())
    path = parsed.path.rstrip("/")
    if not path:
        return ""
    last = path.rsplit("/", 1)[-1]
    lowered = last.lower()
    for ext in _URL_EXTENSIONS:
        if lowered.endswith(ext):
            last = last[: -len(ext)]
            break
    return last.lower()


def _name_tokens(name):
    """Lowercase alpha-numeric tokens used for fuzzy slug matching, with
    German umlaut transliteration and filler words removed. JTL-Shop reorders
    name parts in its SEO slug (e.g. „BH Schaltauge 053" becomes
    `schaltauge-053-fuer-bh`), so order-insensitive token matching is the
    only way to recover the link."""
    text = (name or "").lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    return {tok for tok in re.findall(r"[a-z0-9]+", text) if tok not in _FILLER_TOKENS}


class JtlImageSync(models.Model):
    _name = "jtl.image.sync"
    _description = "JTL Shop Image Sync"

    name = fields.Char(default="JTL Shop Image Sync", required=True)
    sitemap_url = fields.Char(
        string="Shop Sitemap URL",
        help="JTL-Shop sitemap URL — sitemap index or urlset, .xml or .xml.gz. "
        "The sitemap must include <image:image> entries.",
    )
    batch_size = fields.Integer(default=300, required=True, help="Products processed per run.")
    overwrite_existing = fields.Boolean(
        string="Overwrite existing images",
        default=False,
        help="When off, products that already have a main image keep it.",
    )
    import_gallery = fields.Boolean(string="Import gallery images", default=True)
    placeholder_markers = fields.Char(
        string="Platzhalter-Bild-Marker",
        default=_DEFAULT_PLACEHOLDER_IMAGE_MARKERS,
        help=(
            "Komma-getrennte Substrings (case-insensitive). Taucht einer davon in "
            "einer Bild-URL aus der Sitemap auf, gilt das Produkt als ohne Bild - "
            "Odoos Standard-Platzhalter bleibt sichtbar. Beispiel: "
            "keinBild,no_picture"
        ),
    )
    shop_search_url_template = fields.Char(
        string="Shop-Such-URL (Fallback)",
        default="",
        help=(
            "Optional. Such-URL mit {sku}-Platzhalter. Wenn die Sitemap das "
            "Produkt nicht enthaelt, ruft der Sync diese URL mit der "
            "Artikelnummer auf und liest das og:image der Antwort. "
            "Beispiele: "
            "JTL-Shop 5: https://shop.example.com/search/?qs={sku} | "
            "Shopware 6: https://shop.example.com/search?search={sku} | "
            "WooCommerce: https://shop.example.com/?s={sku}. "
            "Leer lassen = Fallback aus."
        ),
    )
    search_fallback_max_per_batch = fields.Integer(
        string="Such-Fallback Limit/Batch",
        default=100,
        help=(
            "Maximale Anzahl HTTP-Anfragen an die Shop-Suche pro Sync-Lauf - "
            "damit der Shop bei vielen Treffern nicht ueberlastet wird."
        ),
    )
    last_run = fields.Datetime(readonly=True)
    last_result = fields.Text(readonly=True)
    pending_count = fields.Integer(
        string="Zu synchronisieren",
        compute="_compute_progress",
        help="Produkte, die noch nicht durch den Sync gelaufen sind.",
    )
    done_count = fields.Integer(
        string="Bereits abgearbeitet",
        compute="_compute_progress",
    )
    total_count = fields.Integer(
        string="Produkte gesamt",
        compute="_compute_progress",
    )
    progress_label = fields.Char(
        string="Fortschritt",
        compute="_compute_progress",
        help="Wieviele Produkte wurden bereits durch den Sync verarbeitet.",
    )
    progress_percentage = fields.Float(
        string="Fortschritt %",
        compute="_compute_progress",
        aggregator=False,
    )
    unmatched_count = fields.Integer(
        string="Ohne Bild",
        compute="_compute_progress",
        help="Produkte, die der Sync verarbeitet hat, für die aber kein Treffer "
        "in der Shop-Sitemap gefunden wurde — sie haben weiterhin kein Hauptbild.",
    )
    unmatched_product_ids = fields.Many2many(
        "product.template",
        string="Produkte ohne Bild-Treffer",
        compute="_compute_unmatched_products",
        help="Erste 500 Produkte, die durch den Sync gelaufen sind, aber kein Bild "
        "aus der Sitemap erhalten haben — z. B. weil der Name vom JTL-Shop-Slug "
        "abweicht.",
    )

    @api.depends_context("uid")
    def _compute_unmatched_products(self):
        Template = self.env["product.template"]
        for record in self:
            # Cap to 500 so the form load stays responsive on shops with
            # tens of thousands of templates. The smart-button count still
            # shows the full number.
            record.unmatched_product_ids = Template.search(
                [("jtl_image_sync_done", "=", True), ("image_1920", "=", False)],
                limit=500,
                order="name asc",
            )

    @api.depends_context("uid")
    def _compute_progress(self):
        # Recomputed on every read so the user always sees the live number
        # without storing it on disk (the values change implicitly as
        # product.template records get processed).
        Template = self.env["product.template"]
        for record in self:
            done = Template.search_count([("jtl_image_sync_done", "=", True)])
            pending = Template.search_count([("jtl_image_sync_done", "=", False)])
            unmatched = Template.search_count([
                ("jtl_image_sync_done", "=", True),
                ("image_1920", "=", False),
            ])
            total = done + pending
            record.done_count = done
            record.pending_count = pending
            record.unmatched_count = unmatched
            record.total_count = total
            record.progress_percentage = (done * 100.0 / total) if total else 0.0
            if total:
                record.progress_label = (
                    "%s von %s verarbeitet (%.1f %%) — %s noch offen · %s ohne Bild"
                    % (done, total, record.progress_percentage, pending, unmatched)
                )
            else:
                record.progress_label = "Keine Produkte vorhanden"

    def action_open_pending_products(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Noch zu synchronisierende Produkte"),
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [("jtl_image_sync_done", "=", False)],
        }

    def action_open_done_products(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Bereits synchronisierte Produkte"),
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [("jtl_image_sync_done", "=", True)],
        }

    def action_open_unmatched_products(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Produkte ohne Bild-Treffer"),
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [
                ("jtl_image_sync_done", "=", True),
                ("image_1920", "=", False),
            ],
        }

    def _fetch_sitemap(self, url, markers=(), _depth=0):
        """Return {slug_lower: [image_url, ...]} from a sitemap (index or urlset).

        ``markers`` is a tuple of lowercase substrings — any image URL whose
        path matches one of them is filtered out (placeholder/keinBild gif).
        """
        if _depth > 3:
            return {}
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        raw = urlopen(request, timeout=30).read()
        if url.lower().endswith(".gz") or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        # Strip a UTF-8 BOM if the server sent one — ElementTree.fromstring
        # accepts it on some Python versions and chokes on others.
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            # The most common cause of "mismatched tag" on a sitemap URL is
            # that the server returned an HTML error/redirect page (e.g.
            # because the path is wrong) instead of XML. Surface a snippet
            # of the body so the user can see what came back.
            snippet = raw[:200].decode("utf-8", errors="replace").strip().replace("\n", " ")
            hint = ""
            lowered = snippet.lower()
            if "<!doctype html" in lowered or "<html" in lowered:
                hint = " The response looks like HTML — verify the sitemap URL (JTL shops typically expose /export/sitemap_index.xml)."
            raise ValueError(
                "XML parse error from %s — %s.%s First bytes: %s" % (url, exc, hint, snippet)
            )
        tag = root.tag.rsplit("}", 1)[-1]
        result = {}
        if tag == "sitemapindex":
            for loc in root.findall("s:sitemap/s:loc", _SITEMAP_NS):
                if loc is not None and loc.text:
                    result.update(self._fetch_sitemap(loc.text.strip(), markers, _depth + 1))
            return result
        for url_node in root.findall("s:url", _SITEMAP_NS):
            loc = url_node.find("s:loc", _SITEMAP_NS)
            if loc is None or not loc.text:
                continue
            # Use the shop-agnostic helper so URLs with `.html`/`.php`/query
            # strings (Magento, Shopware, ...) yield the same slug we get
            # for a plain JTL URL.
            slug = _url_to_slug(loc.text)
            if not slug:
                continue
            images = [
                img.text.strip()
                for img in url_node.findall("image:image/image:loc", _SITEMAP_NS)
                if img.text and not _is_placeholder_image_url(img.text.strip(), markers)
            ]
            # If the only entries were placeholder gfx (e.g. /gfx/keinBild.gif)
            # the product genuinely has no shop image — do not register it.
            if images:
                result[slug] = images
        return result

    @staticmethod
    def _download_image(url):
        # Reject anything other than http(s) so a crafted og:image cannot
        # turn this into a local-file or intranet read (SSRF).
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("Only http/https image URLs allowed: %s" % url)
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        return base64.b64encode(urlopen(request, timeout=20).read())

    @staticmethod
    def _extract_og_image(html, page_url):
        """Pull the og:image meta tag out of an HTML page, returning an
        absolute URL or None. Handles both attribute orders (property first
        vs. content first) since shop templates differ."""
        if not html:
            return None
        og_url = None
        for pattern in (
            r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
            r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']',
        ):
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                og_url = match.group(1).strip()
                break
        if not og_url:
            return None
        if og_url.startswith("//"):
            og_url = "https:" + og_url
        elif og_url.startswith("/"):
            parsed = urlsplit(page_url)
            og_url = "%s://%s%s" % (parsed.scheme, parsed.netloc, og_url)
        return og_url

    def _search_shop_for_sku(self, sku, markers):
        """Fallback when sitemap+fuzzy match yielded nothing: hit the shop's
        search URL with the SKU and read og:image off the response. Many
        shops (JTL Shop in particular) redirect single-match SKU searches
        straight to the product page, so the meta tag points at the real
        product image.

        Returns a (images, reason) tuple:
          * (images, None) on success
          * ([], "non_html") if the response wasn't HTML
          * ([], "no_og_image") if no og:image was in the page
          * ([], "placeholder") if og:image matched the placeholder list
          * Raises on network errors (caller catches and counts as error).
        """
        template = (self.shop_search_url_template or "").strip()
        if not template or not sku:
            return [], "no_template_or_sku"
        try:
            url = template.format(sku=sku)
        except (KeyError, IndexError, ValueError) as exc:
            _logger.warning("JTL image sync: search URL format failed sku=%s: %s", sku, exc)
            return [], "bad_url"
        if urlsplit(url).scheme not in ("http", "https"):
            _logger.warning("JTL image sync: search URL has unsupported scheme: %s", url)
            return [], "bad_url"
        request = Request(url, headers=_BROWSER_HEADERS)
        with urlopen(request, timeout=15) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "html" not in content_type:
                _logger.info(
                    "JTL image sync: search sku=%s — response is %s (not HTML) at %s",
                    sku, content_type or "<empty>", url,
                )
                return [], "non_html"
            # 500 KB cap — enough for any <head> with og tags, and a
            # safety net against very large search results pages.
            raw = response.read(500_000)
            effective_url = response.geturl() or url
        html = raw.decode("utf-8", errors="replace")
        og_image = self._extract_og_image(html, effective_url)
        if not og_image:
            _logger.info(
                "JTL image sync: search sku=%s — no og:image at %s (response %s bytes, head: %r)",
                sku, effective_url, len(raw), html[:300].replace("\n", " "),
            )
            return [], "no_og_image"
        if _is_placeholder_image_url(og_image, markers):
            _logger.info(
                "JTL image sync: search sku=%s — og:image is placeholder (%s)",
                sku, og_image,
            )
            return [og_image], "placeholder"
        _logger.info(
            "JTL image sync: search sku=%s — og:image found: %s (via %s)",
            sku, og_image, effective_url,
        )
        return [og_image], None

    def _run_batch(self):
        self.ensure_one()
        if not self.sitemap_url:
            self.last_result = _("No sitemap URL configured.")
            return
        templates = self.env["product.template"].search(
            [("jtl_image_sync_done", "=", False)], limit=max(self.batch_size, 1)
        )
        if not templates:
            self.write({
                "last_run": fields.Datetime.now(),
                "last_result": _("Nothing to sync — all products have been processed."),
            })
            return
        try:
            markers = _parse_placeholder_markers(self.placeholder_markers)
            slug_map = self._fetch_sitemap(self.sitemap_url.strip(), markers)
        except (URLError, ET.ParseError, ValueError, OSError) as exc:
            self.write({
                "last_run": fields.Datetime.now(),
                "last_result": _("Sitemap fetch failed: %s") % exc,
            })
            return
        # Build an inverted token index over the sitemap once per batch so
        # the fuzzy fallback below stays O(tokens_per_product) instead of
        # iterating all ~20k slugs for every product.
        token_index = defaultdict(set)
        for slug in slug_map:
            for token in _name_tokens(slug):
                token_index[token].add(slug)
        gallery_supported = "product.image" in self.env and "product_template_image_ids" in self.env["product.template"]._fields
        matched = main_set = gallery_added = no_match = errors = fuzzy_matched = search_matched = 0
        search_attempts = search_errors = search_skipped_no_sku = 0
        search_placeholder = search_no_og = search_non_html = 0
        search_template_configured = bool((self.shop_search_url_template or "").strip())
        search_budget = max(self.search_fallback_max_per_batch, 0)
        _logger.info(
            "JTL image sync: starting batch — search_fallback %s, budget %s",
            "ON" if search_template_configured else "OFF",
            search_budget,
        )
        for template in templates:
            images = slug_map.get(slugify_name(template.name))
            if not images:
                # Fuzzy fallback: JTL-Shop reorders name parts in the slug
                # (e.g. "BH Schaltauge 053" → "schaltauge-053-fuer-bh"), so
                # match on the token set instead of the exact string.
                name_tokens = _name_tokens(template.name)
                if name_tokens:
                    candidates = None
                    for token in name_tokens:
                        token_slugs = token_index.get(token)
                        if not token_slugs:
                            candidates = None
                            break
                        candidates = token_slugs if candidates is None else (candidates & token_slugs)
                        if not candidates:
                            break
                    # Only accept the match when it is unambiguous — two
                    # candidates would mean we cannot tell which product
                    # this sitemap entry belongs to.
                    if candidates and len(candidates) == 1:
                        images = slug_map[next(iter(candidates))]
                        fuzzy_matched += 1
            if not images and search_template_configured and search_budget > 0:
                # Last resort: ask the shop's search endpoint for this SKU.
                # JTL-Shop redirects single-match SKU searches straight to
                # the product page, so og:image hits the right image.
                sku = template.default_code or (
                    template.product_variant_ids[:1].default_code if template.product_variant_ids else False
                )
                if not sku:
                    search_skipped_no_sku += 1
                    _logger.info(
                        "JTL image sync: search fallback skipped — no SKU on template %s (id=%s)",
                        template.display_name, template.id,
                    )
                else:
                    search_budget -= 1
                    search_attempts += 1
                    try:
                        result_images, reason = self._search_shop_for_sku(sku, markers)
                    except (URLError, OSError, ValueError) as exc:
                        search_errors += 1
                        _logger.warning(
                            "JTL image sync: search HTTP error sku=%s name=%s: %s",
                            sku, template.display_name, exc,
                        )
                        result_images, reason = [], "http_error"
                    except Exception as exc:  # pragma: no cover - defensive
                        search_errors += 1
                        _logger.warning(
                            "JTL image sync: search unexpected error sku=%s name=%s: %s",
                            sku, template.display_name, exc,
                        )
                        result_images, reason = [], "unexpected"
                    if reason == "placeholder":
                        search_placeholder += 1
                    elif reason == "no_og_image":
                        search_no_og += 1
                    elif reason == "non_html":
                        search_non_html += 1
                    if reason is None and result_images:
                        images = result_images
                        search_matched += 1
            if not images:
                no_match += 1
                template.jtl_image_sync_done = True
                continue
            matched += 1
            try:
                if self.overwrite_existing or not template.image_1920:
                    template.image_1920 = self._download_image(images[0])
                    main_set += 1
                if self.import_gallery and gallery_supported and not template.product_template_image_ids:
                    for index, img_url in enumerate(images[1:], start=1):
                        self.env["product.image"].create({
                            "name": "%s %s" % (template.name or template.default_code or "", index),
                            "product_tmpl_id": template.id,
                            "image_1920": self._download_image(img_url),
                        })
                        gallery_added += 1
            except (URLError, ValueError, OSError) as exc:
                errors += 1
                _logger.warning("JTL image sync failed for %s: %s", template.display_name, exc)
            template.jtl_image_sync_done = True
        search_info = ""
        if search_template_configured:
            search_info = _(
                " | Shop-Suche: %(attempts)s Versuche → %(hit)s Treffer, "
                "%(placeholder)s Platzhalter, %(no_og)s ohne og:image, "
                "%(non_html)s nicht-HTML, %(err)s HTTP-Fehler, "
                "%(no_sku)s ohne SKU"
            ) % {
                "attempts": search_attempts,
                "hit": search_matched,
                "placeholder": search_placeholder,
                "no_og": search_no_og,
                "non_html": search_non_html,
                "err": search_errors,
                "no_sku": search_skipped_no_sku,
            }
        self.write({
            "last_run": fields.Datetime.now(),
            "last_result": _(
                "Batch of %(total)s processed — %(matched)s matched "
                "(%(fuzzy)s via fuzzy token match, %(search)s via Shop-Suche), "
                "%(main)s main images set, %(gallery)s gallery images added, "
                "%(nomatch)s without a sitemap match, %(errors)s errors.%(search_info)s"
            ) % {
                "total": len(templates),
                "matched": matched,
                "fuzzy": fuzzy_matched,
                "search": search_matched,
                "main": main_set,
                "gallery": gallery_added,
                "nomatch": no_match,
                "errors": errors,
                "search_info": search_info,
            },
        })

    def action_run_now(self):
        for record in self:
            record._run_batch()
        return True

    def action_reset_sync(self):
        self.env["product.template"].search([("jtl_image_sync_done", "=", True)]).write(
            {"jtl_image_sync_done": False}
        )
        for record in self:
            record.last_result = _("Sync state reset — all products will be reprocessed on the next run.")
        return True

    @api.model
    def cron_sync_images(self):
        record = self.search([], limit=1)
        if record:
            record._run_batch()
