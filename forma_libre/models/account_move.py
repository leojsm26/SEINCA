from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    # Campos en moneda base
    tax_16 = fields.Monetary(string="IVA 16%", compute="_compute_tax_amounts", store=True)
    tax_8 = fields.Monetary(string="IVA 8%", compute="_compute_tax_amounts", store=True)
    tax_31 = fields.Monetary(string="IVA 31%", compute="_compute_tax_amounts", store=True)
    tax_exento = fields.Monetary(string="Monto Exento", compute="_compute_tax_amounts", store=True)
    base_16 = fields.Monetary(string="Base Imponible 16%", compute="_compute_tax_amounts", store=True)
    base_8 = fields.Monetary(string="Base Imponible 8%", compute="_compute_tax_amounts", store=True)
    base_31 = fields.Monetary(string="Base Imponible 31%", compute="_compute_tax_amounts", store=True)
    total_imponible = fields.Monetary(string="Total Imponible", compute="_compute_tax_amounts", store=True)
    subtotal = fields.Monetary(string="SubTotal", compute="_compute_tax_amounts", store=True)

    # Campos en dólares
    tax_16_usd = fields.Monetary(string="IVA 16% (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')
    tax_8_usd = fields.Monetary(string="IVA 8% (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')
    tax_31_usd = fields.Monetary(string="IVA 31% (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')
    tax_exento_usd = fields.Monetary(string="Monto Exento (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')
    base_16_usd = fields.Monetary(string="Base Imponible 16% (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')
    base_8_usd = fields.Monetary(string="Base Imponible 8% (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')
    base_31_usd = fields.Monetary(string="Base Imponible 31% (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')
    total_imponible_usd = fields.Monetary(string="Total Imponible (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')
    subtotal_usd = fields.Monetary(string="SubTotal (USD)", compute="_compute_tax_amounts", store=True, currency_field='usd_currency_id')

    # Moneda USD
    usd_currency_id = fields.Many2one('res.currency', string="Moneda USD", default=lambda self: self.env.ref('base.USD'))

    # Control de impresión
    invoice_template_dual_printed = fields.Boolean(string="Factura Original Impresa", default=False, readonly=True)
    print_count = fields.Integer(string="Cantidad de impresiones", copy=False, default=0)

    # Control para visibilidad del botón
    show_print_dual_button = fields.Boolean(
        string="Mostrar botón de factura dual",
        compute="_compute_show_print_dual_button",
        store=False
    )

    @api.depends('move_type')
    def _compute_show_print_dual_button(self):
        for rec in self:
            rec.show_print_dual_button = rec.move_type == 'out_invoice'

    @api.depends('line_ids.price_subtotal', 'line_ids.tax_ids', 'line_ids.price_total')
    def _compute_tax_amounts(self):
        for move in self:
            tax_16 = tax_8 = tax_31 = tax_exento = 0.0
            base_16 = base_8 = base_31 = 0.0
            tax_16_usd = tax_8_usd = tax_31_usd = tax_exento_usd = 0.0
            base_16_usd = base_8_usd = base_31_usd = 0.0

            for line in move.line_ids.filtered(lambda l: l.display_type == 'product'):
                subtotal = line.price_subtotal or 0.0
                subtotal_usd = getattr(line, 'price_subtotal_usd', 0.0) or 0.0

                tax_list = line.tax_ids.filtered(lambda t: t.amount_type == 'percent')

                if not tax_list or all(t.amount == 0 for t in tax_list):
                    tax_exento += subtotal
                    tax_exento_usd += subtotal_usd
                else:
                    for tax in tax_list:
                        rate = tax.amount
                        if rate == 16:
                            base_16 += subtotal
                            tax_16 += subtotal * (rate / 100)
                            base_16_usd += subtotal_usd
                            tax_16_usd += subtotal_usd * (rate / 100)
                        elif rate == 8:
                            base_8 += subtotal
                            tax_8 += subtotal * (rate / 100)
                            base_8_usd += subtotal_usd
                            tax_8_usd += subtotal_usd * (rate / 100)
                        elif rate == 31:
                            base_31 += subtotal
                            tax_31 += subtotal * (rate / 100)
                            base_31_usd += subtotal_usd
                            tax_31_usd += subtotal_usd * (rate / 100)

            move.tax_16 = tax_16
            move.tax_8 = tax_8
            move.tax_31 = tax_31
            move.tax_exento = tax_exento
            move.base_16 = base_16
            move.base_8 = base_8
            move.base_31 = base_31
            move.total_imponible = base_16 + base_8 + base_31
            move.subtotal = tax_exento + move.total_imponible + tax_16 + tax_8 + tax_31

            move.tax_16_usd = tax_16_usd
            move.tax_8_usd = tax_8_usd
            move.tax_31_usd = tax_31_usd
            move.tax_exento_usd = tax_exento_usd
            move.base_16_usd = base_16_usd
            move.base_8_usd = base_8_usd
            move.base_31_usd = base_31_usd
            move.total_imponible_usd = base_16_usd + base_8_usd + base_31_usd
            move.subtotal_usd = tax_exento_usd + move.total_imponible_usd + tax_16_usd + tax_8_usd + tax_31_usd

    def get_report_name(self):
        return f"Factura Fiscal - {self.name}"

    def action_print_invoice_custom_dual(self):
        # Verificar si el movimiento está en estado 'posted'
        if self.state == 'posted':
            # Verificar si el tipo de movimiento es una nota de crédito o débito
            if self.move_type == 'out_refund':  # Nota de Crédito
                return self.env.ref('forma_libre.action_report_invoice_custom_dual').report_action(self)
            elif self.debit_origin_id:  # Nota de Débito (cuando debit_origin_id está establecido)
                return self.env.ref('forma_libre.action_report_invoice_custom_dual').report_action(self)
            else:
                # Es una factura normal
                return self.env.ref('forma_libre.action_report_invoice_custom_dual').report_action(self)
        else:
            # Si el movimiento no está publicado, lanzar un error con el mensaje correspondiente
            if self.move_type == 'out_refund':
                message = 'La Nota de Crédito debe estar publicada para ser impresa.'
            elif self.debit_origin_id:
                message = 'La Nota de Débito debe estar publicada para ser impresa.'
            else:
                message = 'La factura debe estar publicada para ser impresa.'
            
            raise UserError(message)
    
    @api.model
    def create(self, vals):
        vals['invoice_template_dual_printed'] = False
        return super().create(vals)

    def copy(self, default=None):
        default = dict(default or {}, invoice_template_dual_printed=False)
        return super().copy(default)
    
class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    alicuota = fields.Char(string='Alícuota', compute='_compute_alicuota', store=True)

    @api.depends('tax_ids')
    def _compute_alicuota(self):
        for line in self:
            if not line.tax_ids:
                line.alicuota = '( E )'
            else:
                all_zero = all(tax.amount == 0 for tax in line.tax_ids)
                line.alicuota = '( E )' if all_zero else ''

    
