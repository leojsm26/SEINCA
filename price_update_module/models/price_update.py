from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _set_currency_usd_id(self):
        usd = self.env.ref('base.USD', raise_if_not_found=False)
        return usd

    standard_price_usd = fields.Float('Costo USD', digits='Product Price', default=0.0) #new
    list_price_usd = fields.Float('Sale Price USD', digits='Product Price', required=True, default=0.0)
    currency_usd_id = fields.Many2one('res.currency', 'USD', default=_set_currency_usd_id)

    @api.onchange('list_price_usd')
    def onchange_price_bs(self):
        for product in self:
            company = product.company_id or self.env.company
            rate = 1.0
            if product.currency_usd_id:
                company_rate = self.env['res.currency.rate'].search([
                    ('currency_id', '=', product.currency_usd_id.id),
                    ('company_id', '=', company.id),
                    ('name', '<=', fields.Date.today()),
                ], order='name desc, id desc', limit=1)
                rate = company_rate.inverse_company_rate if company_rate else 0.0

                if not rate:
                    shared_rate = self.env['res.currency.rate'].search([
                        ('currency_id', '=', product.currency_usd_id.id),
                        ('company_id', '=', False),
                        ('name', '<=', fields.Date.today()),
                    ], order='name desc, id desc', limit=1)
                    rate = shared_rate.inverse_company_rate if shared_rate else 1.0

            new_price = product.list_price_usd * rate
            product.list_price = new_price
            for variant in product.product_variant_ids:
                variant.list_price = new_price


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    def _set_currency_usd_id(self):
        usd = self.env.ref('base.USD', raise_if_not_found=False)
        return usd

    list_price_usd = fields.Float('Valor Precio Extra $', digits='Product Price', required=True, default=0.0)
    currency_usd_id = fields.Many2one('res.currency', 'USD', default=_set_currency_usd_id)


class ProductPricelist(models.Model):
    _inherit = 'product.pricelist'

    rate = fields.Float(
        string='Tasa Compañía',
        help='Tasa de cambio utilizada para calcular los precios.',
        compute='_compute_rate',
        store=False,
        digits=(12, 2)
    )

    @api.model
    def _get_latest_inverse_rate(self, currency, company, date_value=None):
        if not currency:
            return 0.0

        date_value = date_value or fields.Date.today()
        rate_model = self.env['res.currency.rate']

        if company:
            company_rate = rate_model.search([
                ('currency_id', '=', currency.id),
                ('company_id', '=', company.id),
                ('name', '<=', date_value),
            ], order='name desc, id desc', limit=1)
            if company_rate:
                return company_rate.inverse_company_rate

        shared_rate = rate_model.search([
            ('currency_id', '=', currency.id),
            ('company_id', '=', False),
            ('name', '<=', date_value),
        ], order='name desc, id desc', limit=1)
        return shared_rate.inverse_company_rate if shared_rate else 0.0

    @api.depends_context('company')
    def _compute_rate(self):
        usd_currency = self.env.ref('base.USD', raise_if_not_found=False)
        if not usd_currency:
            for record in self:
                record.rate = 0.0
            return

        for pricelist in self:
            company = pricelist.company_id or self.env.company
            pricelist.rate = self._get_latest_inverse_rate(
                currency=usd_currency,
                company=company,
                date_value=fields.Date.today(),
            )

    @api.model
    def cron_update_prices_from_usd_rate(self):
        _logger.info("== INICIANDO CRON DE ACTUALIZACIÓN DE TARIFAS ==")
        usd_currency = self.env.ref('base.USD', raise_if_not_found=False)
        if not usd_currency:
            _logger.warning("Moneda USD no encontrada.")
            return

        today = fields.Date.today()
        pricelists = self.search([])
        for pricelist in pricelists:
            company = pricelist.company_id or self.env.company
            company_rate = self._get_latest_inverse_rate(
                currency=usd_currency,
                company=company,
                date_value=today,
            )

            if not company_rate:
                _logger.warning(
                    "No se encontro tasa USD para tarifa %s (compania %s).",
                    pricelist.display_name,
                    company.display_name,
                )
                continue

            pricelist.rate = company_rate
            for item in pricelist.item_ids:
                if item.price_usd:
                    item.fixed_price = item.price_usd * company_rate
                    _logger.info(f"Actualizado item {item.id} con precio fijo: {item.fixed_price}")

        # 2. Actualizar costo estándar en product.template
        products = self.env['product.template'].search([('standard_price_usd', '>', 0)])
        for product in products:
            company = product.company_id or self.env.company
            company_rate = self._get_latest_inverse_rate(
                currency=usd_currency,
                company=company,
                date_value=today,
            )
            if not company_rate:
                continue

            old_cost = product.standard_price
            new_cost = product.standard_price_usd * company_rate
            product.standard_price = new_cost
            _logger.info(f"[Producto] {product.name} (ID: {product.id}) - Costo: {old_cost} -> {new_cost}")
        _logger.info("== FINALIZA CRON DE ACTUALIZACIÓN DE TARIFAS ==")

class ProductPricelistItem(models.Model):
    _inherit = 'product.pricelist.item'

    price_usd = fields.Float(
        string='Precio $',
        digits=(12, 3),
        help='Precio en dólares que se usará para calcular el precio fijo.'
    )

    @api.onchange('price_usd')
    def _onchange_price_usd(self):
        """
        Cada vez que cambie el precio en USD, se recalcula el precio fijo.
        """
        for item in self:
            if item.pricelist_id and item.pricelist_id.rate:
                item.fixed_price = item.price_usd * item.pricelist_id.rate

    def update_prices_from_rate(self):
        for item in self:
            rate = item.pricelist_id.rate if item.pricelist_id else 0.0
            if item.price_usd and rate:
                item.fixed_price = item.price_usd * rate
