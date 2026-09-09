from odoo import models, fields, api
from datetime import date

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    tax_today = fields.Float(string="Tasa", compute="_compute_tax_today", store=True, digits=(16, 4))

    currency_id_dif = fields.Many2one(
        "res.currency", string="Moneda Ref.",
        compute="_compute_currency_id_dif", store=True
    )

    amount_untaxed_dual = fields.Monetary(
        string="Base Imponible Moneda Ref.",
        currency_field='currency_id_dif',
        compute='_compute_dual_totals',
        store=True
    )
    amount_tax_dual = fields.Monetary(
        string="Impuesto Moneda Ref.",
        currency_field='currency_id_dif',
        compute='_compute_dual_totals',
        store=True
    )
    amount_total_dual = fields.Monetary(
        string="Total Moneda Ref.",
        currency_field='currency_id_dif',
        compute='_compute_dual_totals',
        store=True
    )

    @api.depends('currency_id')
    def _compute_currency_id_dif(self):
        usd = self.env.ref('base.USD')
        ves = self.env.ref('base.VEF')  
        for order in self:
            order.currency_id_dif = ves if order.currency_id == usd else usd

    @api.depends('company_id', 'date_order', 'order_line', 'order_line.product_qty')
    def _compute_tax_today(self):
        CurrencyRate = self.env['res.currency.rate']
        usd = self.env.ref('base.USD')
        ves = self.env.ref('base.VEF')  # O VES según tu sistema

        for order in self:
            if not order.company_id or not order.date_order:
                order.tax_today = 1.0
                continue

            # Si el presupuesto se modifica (estado borrador/enviado), usar fecha actual
            search_date = order.date_order
            if order.state in ['draft', 'sent', 'to approve']:
                search_date = fields.Datetime.now()
            
            # Siempre buscar la tasa USD → VEF
            rate = CurrencyRate.search([
                ('currency_id', '=', usd.id),
                ('name', '<=', search_date),
            ], order='name desc', limit=1)

            order.tax_today = rate.inverse_company_rate if rate else 1.0


    @api.depends('amount_untaxed', 'amount_tax', 'currency_id', 'tax_today')
    def _compute_dual_totals(self):
        usd = self.env.ref('base.USD')
        for order in self:
            if not order.tax_today:
                order.amount_untaxed_dual = 0
                order.amount_tax_dual = 0
                order.amount_total_dual = 0
                continue

            if order.currency_id == usd:
                factor = order.tax_today  # USD → Bs
            else:
                factor = 1 / order.tax_today  # Bs → USD

            order.amount_untaxed_dual = order.amount_untaxed * factor
            order.amount_tax_dual = order.amount_tax * factor
            order.amount_total_dual = order.amount_untaxed_dual + order.amount_tax_dual

