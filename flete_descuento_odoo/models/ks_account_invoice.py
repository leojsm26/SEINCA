from odoo import api, fields, models,_
from odoo.exceptions import UserError, ValidationError
import logging
from pprint import pprint
import sys
from odoo import models, fields, api,_
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_date, formatLang
import psycopg2
import re
from textwrap import shorten
from contextlib import ExitStack, contextmanager
from odoo import api, fields, models, _, Command
from odoo.addons.account.tools import format_structured_reference_iso
from odoo.exceptions import UserError, ValidationError, AccessError, RedirectWarning
from odoo.tools import (
    date_utils,
    email_re,
    email_split,
    float_compare,
    float_is_zero,
    float_repr,
    format_amount,
    format_date,
    formatLang,
    frozendict,
    get_lang,
    groupby,
    index_exists,
    is_html_empty,
)
_logger = logging.getLogger(__name__)


class KsGlobalTaxInvoice(models.Model):
    _inherit = "account.move"

    ks_global_tax_rate = fields.Float(string='Flete (%):')
    ks_amount_global_tax = fields.Monetary(string="Flete", readonly=True, compute='flete_descuento_odoo',
                                           tracking=True, store=True)
    flete_bs = fields.Monetary(string="Flete Bs", readonly=True, compute='flete_descuento_odoo',
                                           tracking=True, store=True)
    ks_enable_tax = fields.Boolean(compute='ks_verify_tax')
    trm_invoice = fields.Boolean('TRM Factura origen')
    ks_sales_tax_account_id = fields.Integer(compute='ks_verify_tax')
    ks_purchase_tax_account_id = fields.Integer(compute='ks_verify_tax')

    enable_invoice_discount = fields.Selection(selection=[
                                                ('value', 'Por Valor'),
                                                ('percent', 'Por Porcentaje'),
                                            ],string='Descuento Pie de Factura',default='percent')
    type_invoice_discount = fields.Selection(selection=[('linea', 'Por linea'),
                                                        ('global', 'Global'),
                                                    ],string='Descuento Pie de Factura',default='global')

    invoice_discount = fields.Monetary(string='Desc. De Factura $', store=True, compute="flete_descuento_odoo")
    invoice_discount_view = fields.Monetary(related="invoice_discount",string='Desc. De Factura',store=False)
    invoice_discount_percent = fields.Float(string='Desc. De Factura %',  store=True)
    invoice_discount_tax = fields.Selection(selection=[('00', 'Descuento Base Impuesto'),
                                                        ('01', 'Descuento Sin Base Impuesto')],string='Motivo de Descuento',default='00')
    sales_discount_account = fields.Many2one('account.account', related='company_id.sales_discount_account', string="Sales Tax Account")
    purchase_discount_account = fields.Many2one('account.account', related='company_id.purchase_discount_account',string="Purchase Tax Account")
    invoice_untaxed_bs = fields.Monetary(string='Monto sin impuestos', store=True, compute="_untaxed_bs")

    @api.depends('ks_global_tax_rate','invoice_discount_percent','amount_untaxed_bs','flete_bs')
    def _untaxed_bs(self):
        for rec in self:
            rec.invoice_untaxed_bs = rec.amount_untaxed_bs - rec.flete_bs

    @api.depends('company_id.ks_enable_tax')
    def ks_verify_tax(self):
        for rec in self:
            rec.ks_enable_tax = rec.company_id.ks_enable_tax
            rec.ks_sales_tax_account_id = rec.company_id.ks_sales_tax_account.id
            rec.ks_purchase_tax_account_id = rec.company_id.ks_purchase_tax_account.id


    @api.depends(
        'line_ids.matched_debit_ids.debit_move_id.move_id.payment_id.is_matched',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.matched_credit_ids.credit_move_id.move_id.payment_id.is_matched',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.debit',
        'line_ids.credit',
        'line_ids.currency_id',
        'line_ids.amount_currency',
        'line_ids.amount_residual',
        'line_ids.amount_residual_currency',
        'line_ids.payment_id.state',
        'line_ids.full_reconcile_id',
        'enable_invoice_discount',
        'invoice_discount',
        'trm_invoice',
        'invoice_discount_percent',
        'ks_global_tax_rate')
    def _compute_amount(self):
        for move in self:
            total_untaxed, total_untaxed_currency = 0.0, 0.0
            total_tax, total_tax_currency = 0.0, 0.0
            total_residual, total_residual_currency = 0.0, 0.0
            total, total_currency = 0.0, 0.0

            for line in move.line_ids:
                if move.is_invoice(True):
                    # === Invoices ===
                    if line.display_type == 'tax' or (line.display_type == 'rounding' and line.tax_repartition_line_id):
                        # Tax amount.
                        total_tax += line.balance
                        total_tax_currency += line.amount_currency
                        total += line.balance
                        total_currency += line.amount_currency
                    elif line.display_type in ('product', 'rounding') or line.flete_invoice or line.disc_invoice:
                        # Untaxed amount.
                        total_untaxed += line.balance
                        total_untaxed_currency += line.amount_currency
                        total += line.balance
                        total_currency += line.amount_currency
                    elif line.display_type == 'payment_term':
                        # Residual amount.
                        total_residual += line.amount_residual
                        total_residual_currency += line.amount_residual_currency
                else:
                    # === Miscellaneous journal entry ===
                    if line.debit:
                        total += line.balance
                        total_currency += line.amount_currency

            sign = move.direction_sign
            move.amount_untaxed = sign * total_untaxed_currency
            move.amount_tax = sign * total_tax_currency
            move.amount_total = sign * total_currency
            move.amount_residual = -sign * total_residual_currency
            move.amount_untaxed_signed = -total_untaxed
            move.amount_tax_signed = -total_tax
            move.amount_total_signed = abs(total) if move.move_type == 'entry' else -total
            move.amount_residual_signed = total_residual
            move.amount_total_in_currency_signed = abs(move.amount_total) if move.move_type == 'entry' else -(sign * move.amount_total)

    
    @api.depends('invoice_line_ids','enable_invoice_discount','currency_id','partner_id',
                'invoice_discount', 'trm_invoice', 'invoice_discount_percent', 'ks_global_tax_rate')
    def flete_descuento_odoo(self):
        for rec in self:
            total_untaxed = sum(line.price_unit * line.quantity for line in rec.invoice_line_ids if line.display_type in ('product', 'rounding'))
            rec.invoice_discount = 0.0
            if rec.move_type in ['out_invoice','in_invoice', 'in_refund', 'out_refund']:
                base_amount = total_untaxed
                if rec.enable_invoice_discount == 'percent':
                    if rec.type_invoice_discount == 'global' and rec.invoice_discount_percent != 0:
                        rec.invoice_discount = (base_amount * rec.invoice_discount_percent) / 100
                    elif rec.type_invoice_discount == 'linea':
                        discount_amount = sum((line.price_unit - line.price_unit_with_discount) * line.quantity for line in rec.invoice_line_ids)
                        rec.invoice_discount = discount_amount
                
                # if rec.currency_id != rec.company_id.currency_id:
                #     date = rec.reversed_entry_id.date if rec.trm_invoice and rec.reversed_entry_id else rec.date
                #     rec.invoice_discount_signed = rec.currency_id._convert(
                #         rec.invoice_discount, rec.company_id.currency_id, rec.company_id, date or fields.Date.context_today(rec)
                #     )
                # else:
                #     rec.invoice_discount_signed = rec.invoice_discount
            rec.ks_amount_global_tax = ((abs(total_untaxed) - abs(rec.invoice_discount)) * rec.ks_global_tax_rate) / 100
            amount_company_currency = rec.ks_amount_global_tax
            if rec.currency_id != rec.company_id.currency_id:
                date = rec.date 
                if rec.trm_invoice and rec.reversed_entry_id:
                    date = rec.reversed_entry_id.date
                amount_company_currency = rec.currency_id._convert(
                    amount_company_currency,
                    rec.company_id.currency_id,
                    rec.company_id,
                    date or fields.Date.context_today(rec),
                )
            rec.flete_bs = amount_company_currency

    @api.constrains('ks_global_tax_rate','invoice_discount_percent',)
    def ks_check_tax_value(self):
        if (self.ks_global_tax_rate > 100 or self.ks_global_tax_rate < 0) or (self.invoice_discount_percent > 100 or self.invoice_discount_percent < 0):
            raise ValidationError('No puede ingresar un valor porcentual mayor que 100 o menor que 0.')

    def _recompute_discount_lines(self):
        self.ensure_one()
        existing_disc = self.line_ids.filtered(lambda line: line.disc_invoice)
        disc = "Descuento @ {}$".format(self.invoice_discount) if self.invoice_discount else ''
        
        def _apply_cash_rounding(name, account_id, diff_balance, diff_amount_currency, cash_rounding_line):
            rounding_line_vals = {
                'balance': diff_balance,
                'amount_currency': diff_amount_currency,
                'partner_id': self.partner_id.id,
                'move_id': self.id,
                'currency_id': self.currency_id.id,
                'company_id': self.company_id.id,
                'company_currency_id': self.company_id.currency_id.id,
                'display_type': 'discount',
                'name': name,
                'account_id': account_id,
                'disc_invoice': True,
            }
            if cash_rounding_line:
                cash_rounding_line.write(rounding_line_vals)
            else:
                cash_rounding_line = self.env['account.move.line'].create(rounding_line_vals)

        amount_disc = self.invoice_discount
        if self.move_type in ('out_refund', 'in_invoice'):
            amount_disc = -amount_disc

        if amount_disc != 0:
            if self.currency_id == self.company_id.currency_id:
                disc_amount_currency = disc_balance = amount_disc
            else:
                disc_amount_currency = amount_disc
                disc_balance = self.currency_id._convert(disc_amount_currency, self.company_id.currency_id, self.company_id, self.invoice_date or self.date)
            _apply_cash_rounding(disc, self._get_discount_account_id(), disc_balance, disc_amount_currency, existing_disc)

        if self.invoice_discount == 0.0 and existing_disc:
            existing_disc.unlink()


    def _recompute_flete_lines(self):
        self.ensure_one()
        existing_flete = self.line_ids.filtered(lambda line: line.flete_invoice)
        flete = "Flete @ {}%".format(self.ks_global_tax_rate) if self.ks_global_tax_rate else ''
        
        def _apply_cash_rounding(name, account_id, diff_balance, diff_amount_currency, cash_rounding_line):
            rounding_line_vals = {
                'balance': diff_balance,
                'amount_currency': diff_amount_currency,
                'partner_id': self.partner_id.id,
                'move_id': self.id,
                'currency_id': self.currency_id.id,
                'company_id': self.company_id.id,
                'company_currency_id': self.company_id.currency_id.id,
                'display_type': 'discount',
                'name': name,
                'account_id': account_id,
                'flete_invoice': True,
            }
            if cash_rounding_line:
                cash_rounding_line.write(rounding_line_vals)
            else:
                cash_rounding_line = self.env['account.move.line'].create(rounding_line_vals)

        amount_flete = -self.ks_amount_global_tax
        if self.move_type in ('out_refund', 'in_invoice'):
            amount_flete = -amount_flete

        if amount_flete != 0:
            if self.currency_id == self.company_id.currency_id:
                flete_amount_currency = flete_balance = amount_flete
            else:
                flete_amount_currency = amount_flete
                flete_balance = self.currency_id._convert(flete_amount_currency, self.company_id.currency_id, self.company_id, self.invoice_date or self.date)
            _apply_cash_rounding(flete, self._get_tax_account_id(), flete_balance, flete_amount_currency, existing_flete)

        if self.ks_amount_global_tax == 0.0 and existing_flete:
            existing_flete.unlink()

    @contextmanager
    def _sync_rounding_lines(self, container):
        yield
        for invoice in container['records']:
            if invoice.state != 'posted':
                invoice._recompute_cash_rounding_lines()
                invoice._recompute_discount_lines()
                invoice._recompute_flete_lines()

    def _get_discount_account_id(self):
        account_id = self.sales_discount_account.id if self.move_type in ["out_invoice", "out_refund"] else self.purchase_discount_account.id
        if account_id:
            return account_id
        else:
            raise ValidationError("No hay cuenta de descuento")

    def _get_tax_account_id(self):
        account_id = self.ks_sales_tax_account_id if self.move_type in ["out_invoice", "out_refund"] else self.ks_purchase_tax_account_id
        if account_id:
            return account_id
        else:
            raise ValidationError("No hay cuenta de descuento")

    def _find_existing_tax_line(self):
        return self.line_ids.filtered(lambda line: line.flete_invoice)

    def _find_existing_discount_lines(self):
        return self.line_ids.filtered(lambda line: line.disc_invoice)

    def _remove_duplicate_tax_and_discount_lines(self):
        """
        Remove one of the duplicate tax and discount lines from the invoice, if any.
        """
        existing_tax_lines = self._find_existing_tax_line()
        existing_discount_lines = self._find_existing_discount_lines()
        def remove_one_duplicate(lines):
            if len(lines) > 1: 
                seen_lines = set()
                for line in lines:
                    if line.name in seen_lines:
                        self.line_ids -= line
                        break 
                    seen_lines.add(line.name)

        remove_one_duplicate(existing_tax_lines)
        remove_one_duplicate(existing_discount_lines)

    def _remove_zero_balance_tax_discount_lines(self):
        """
        Remove tax and discount lines from the invoice that have a zero balance.
        """
        tax_account_id = self._get_tax_account_id()
        discount_account_id = self._get_discount_account_id()
        lines_to_remove = self.line_ids.filtered(lambda line: 
            (line.account_id.id in [tax_account_id, discount_account_id]) and 
            (line.balance == 0)
        )

        # Remove the identified lines
        if lines_to_remove:
            self.line_ids -= lines_to_remove

    def _remove_duplicate_tax_and_discount_lines(self):
        """
        Remove one of the duplicate tax and discount lines from the invoice, if any.
        """
        existing_tax_lines = self._find_existing_tax_line()
        existing_discount_lines = self._find_existing_discount_lines()
        def remove_one_duplicate(lines):
            if len(lines) > 1: 
                seen_lines = set()
                for line in lines:
                    if line.name in seen_lines:
                        self.line_ids -= line
                        break 
                    seen_lines.add(line.name)

        remove_one_duplicate(existing_tax_lines)
        remove_one_duplicate(existing_discount_lines)

    def _remove_zero_balance_tax_discount_lines(self):
        """
        Remove tax and discount lines from the invoice that have a zero balance.
        """
        tax_account_id = self._get_tax_account_id()
        discount_account_id = self._get_discount_account_id()
        lines_to_remove = self.line_ids.filtered(lambda line: 
            (line.account_id.id in [tax_account_id, discount_account_id]) and 
            (line.balance == 0)
        )

        # Remove the identified lines
        if lines_to_remove:
            self.line_ids -= lines_to_remove

'''
    @api.depends_context('lang')
    @api.depends(
        'invoice_line_ids.currency_rate',
        'invoice_line_ids.tax_base_amount',
        'invoice_line_ids.tax_line_id',
        'invoice_line_ids.price_total',
        'invoice_line_ids.price_subtotal',
        'invoice_payment_term_id',
        'partner_id',
        'currency_id',
        'enable_invoice_discount',
        'invoice_discount',
        'trm_invoice',
        'invoice_discount_percent',
        'ks_global_tax_rate'
    )
    def _compute_tax_totals(self):
        super()._compute_tax_totals()
        for move in self:
            move.invoice_line_ids._compute_price_unit_with_discount()
            inc_total = move.ks_amount_global_tax
            if move.is_invoice(include_receipts=True) and move.tax_totals:
                if not float_is_zero(inc_total + move.invoice_discount, precision_rounding=move.currency_id.rounding):
                    tax_totals = move.tax_totals
                    if not  any(move.invoice_line_ids.tax_ids):
                        tax_totals['amount_untaxed'] += inc_total
                        tax_totals['formatted_amount_untaxed'] = formatLang(self.env, tax_totals['amount_untaxed'] + inc_total , currency_obj=move.currency_id)
                    tax_totals['amount_total'] += inc_total
                    tax_totals['formatted_amount_total'] = formatLang(self.env, tax_totals['amount_total'] + inc_total , currency_obj=move.currency_id)
                    inc_group = {
                        'tax_group_name': 'Flete',
                        'tax_group_amount': inc_total,
                        'tax_group_base_amount': sum(move.invoice_line_ids.mapped('price_subtotal')),
                        'formatted_tax_group_amount': formatLang(self.env, inc_total, currency_obj=move.currency_id),
                        'formatted_tax_group_base_amount': formatLang(self.env, sum(move.invoice_line_ids.mapped('price_subtotal')), currency_obj=move.currency_id),
                        'tax_group_id': 'flete', 
                    }

                    if 'subtotals' in tax_totals['groups_by_subtotal']:
                        tax_totals['groups_by_subtotal']['subtotals'].append(inc_group)
                    else:
                        first_key = next(iter(tax_totals['groups_by_subtotal']), 'Untaxed Amount')
                        tax_totals['groups_by_subtotal'][first_key].append(inc_group)
                    move.tax_totals = tax_totals


    @api.depends_context('lang')
    @api.depends(
        'invoice_line_ids.currency_rate',
        'invoice_line_ids.tax_base_amount',
        'invoice_line_ids.tax_line_id',
        'invoice_line_ids.price_total',
        'invoice_line_ids.price_subtotal',
        'invoice_payment_term_id',
        'partner_id',
        'currency_id',
        'enable_invoice_discount',
        'invoice_discount',
        'trm_invoice',
        'invoice_discount_percent',
        'ks_global_tax_rate'
    )
    def _compute_tax_totals_custom(self):
        super()._compute_tax_totals_custom()
        for move in self:
            move.invoice_line_ids._compute_price_unit_with_discount()
            calc_currency = move.company_id.currency_id if move.currency_id != move.company_id.currency_id else self.env.ref('base.USD')
            inc_total = move.ks_amount_global_tax
            if move.is_invoice(include_receipts=True) and move.tax_totals_custom:
                if calc_currency != move.currency_id:
                    inc_total =  move.currency_id._convert(
                            inc_total,
                            calc_currency,
                            move.company_id,
                            move.date or fields.Date.today()
                        )
                if not float_is_zero(inc_total + move.invoice_discount, precision_rounding=move.currency_id.rounding):
                    tax_totals = move.tax_totals_custom
                    if not  any(move.invoice_line_ids.tax_ids):
                        tax_totals['amount_untaxed'] += inc_total
                        tax_totals['formatted_amount_untaxed'] = formatLang(self.env, tax_totals['amount_untaxed'] + inc_total , currency_obj=move.currency_id)
                    tax_totals['amount_total'] += inc_total
                    tax_totals['formatted_amount_total'] = formatLang(self.env, tax_totals['amount_total'] + inc_total , currency_obj=move.currency_id)
                    inc_group = {
                        'tax_group_name': 'Flete',
                        'tax_group_amount': inc_total,
                        'tax_group_base_amount': sum(move.invoice_line_ids.mapped('price_subtotal')),
                        'formatted_tax_group_amount': formatLang(self.env, inc_total, currency_obj=move.currency_id),
                        'formatted_tax_group_base_amount': formatLang(self.env, sum(move.invoice_line_ids.mapped('price_subtotal')), currency_obj=move.currency_id),
                        'tax_group_id': 'flete', 
                    }

                    if 'subtotals' in tax_totals['groups_by_subtotal']:
                        tax_totals['groups_by_subtotal']['subtotals'].append(inc_group)
                    else:
                        first_key = next(iter(tax_totals['groups_by_subtotal']), 'Untaxed Amount')
                        tax_totals['groups_by_subtotal'][first_key].append(inc_group)
                    move.tax_totals_custom = tax_totals
'''

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    price_unit_with_discount = fields.Float(compute='_compute_price_unit_with_discount', store=True)
    discount_line = fields.Float('Descuento C. ($)', store=True)
    flete_invoice = fields.Boolean('Línea de Flete')
    disc_invoice = fields.Boolean('Línea de descuento')

    @api.depends(
        'price_unit', 'quantity', 'discount', 'product_id', 'balance', 'tax_ids',
        'currency_id', 'partner_id', 'move_id.partner_id', 'move_id.invoice_discount',
        'move_id.invoice_discount_percent', 'discount_line', 'move_id.type_invoice_discount',
        'move_id.invoice_line_ids.price_unit', 'move_id.invoice_line_ids.quantity',
        'move_id.invoice_line_ids.discount'
    )
    def _compute_price_unit_with_discount(self):
        for rec in self:
            rec._compute_price_unit_with_discount_logic()

    @api.onchange(
        'price_unit', 'quantity', 'discount', 'product_id', 'balance', 'tax_ids',
        'currency_id', 'partner_id', 'move_id.partner_id', 'move_id.invoice_discount',
        'move_id.invoice_discount_percent', 'discount_line', 'move_id.type_invoice_discount',
        'move_id.invoice_line_ids.price_unit', 'move_id.invoice_line_ids.quantity',
        'move_id.invoice_line_ids.discount'
    )
    def _onchange_price_unit_with_discount(self):
        for rec in self:
            rec._compute_price_unit_with_discount_logic()

    def _compute_price_unit_with_discount_logic(self):
        move = self.move_id
        lines = move.invoice_line_ids
        total_invoice_amount = sum(
            line.price_unit * line.quantity for line in lines if line.quantity and line.price_unit
        )
        if total_invoice_amount == 0:
            self.price_unit_with_discount = self.price_unit * (1 - (self.discount / 100.0))
            return
        if move.type_invoice_discount == 'linea':
            self.price_unit_with_discount = self.price_unit * (1 - ((self.discount + self.discount_line) / 100.0))
        else:
            self.price_unit_with_discount = self._get_price_unit_with_discount(total_invoice_amount)

    def _get_price_unit_with_discount(self, total_invoice_amount):
        price_unit_after_line_discount = self.price_unit * (1 - ((self.discount) / 100.0))
        line_proportion = (self.price_unit * self.quantity) / total_invoice_amount
        line_share_of_invoice_discount = self.move_id.invoice_discount * line_proportion
        discount_per_unit = line_share_of_invoice_discount / (self.quantity if self.quantity != 0 else 1)
        price_unit_with_discount = price_unit_after_line_discount - discount_per_unit
        return price_unit_with_discount


    @api.depends('tax_ids', 'currency_id', 'partner_id', 'price_unit_with_discount', 'analytic_distribution', 'balance','move_id.invoice_discount', 'move_id.invoice_discount_percent',  'move_id.partner_id', 'price_unit', 'quantity')
    def _compute_all_tax(self):
        for line in self:
            sign = line.move_id.direction_sign
            if line.display_type == 'tax':
                line.compute_all_tax = {}
                line.compute_all_tax_dirty = False
                continue
            if line.display_type == 'product' and line.move_id.is_invoice(True):
                amount_currency = sign * line.price_unit_with_discount
                handle_price_include = True
                quantity = line.quantity
            else:
                amount_currency = line.amount_currency
                handle_price_include = False
                quantity = 1
            compute_all_currency = line.tax_ids.compute_all(
                amount_currency,
                currency=line.currency_id,
                quantity=quantity,
                product=line.product_id,
                partner=line.move_id.partner_id or line.partner_id,
                is_refund=line.is_refund,
                handle_price_include=handle_price_include,
                include_caba_tags=line.move_id.always_tax_exigible,
                fixed_multiplicator=sign,
            )
            rate = line.tax_today if line.balance else 1
            #rate = line.amount_currency / line.balance if line.balance else 1
            _logger.warning(f"steve Rate: {rate}")
            #_logger.warning(f"steve Rate: {rate}, Tax Amount: {tax['amount']}, Tax Base: {tax['base']}")
            line.compute_all_tax_dirty = True
            line.compute_all_tax = {
                frozendict({
                    'tax_repartition_line_id': tax['tax_repartition_line_id'],
                    'group_tax_id': tax['group'] and tax['group'].id or False,
                    'account_id': tax['account_id'] or line.account_id.id,
                    'currency_id': line.currency_id.id,
                    'analytic_distribution': (tax['analytic'] or not tax['use_in_tax_closing']) and line.analytic_distribution,
                    'tax_ids': [(6, 0, tax['tax_ids'])],
                    'tax_tag_ids': [(6, 0, tax['tag_ids'])],
                    'partner_id': line.move_id.partner_id.id or line.partner_id.id,
                    'move_id': line.move_id.id,
                    'display_type': line.display_type,
                }): {
                    'name': tax['name'] + (' ' + _('(Discount)') if line.display_type == 'epd' else ''),
                    'balance': tax['amount'] * rate, # steve
                    'amount_currency': tax['amount'],
                    'tax_base_amount': tax['base'] * rate * (-1 if line.tax_tag_invert else 1),
                }
                for tax in compute_all_currency['taxes']
                if tax['amount']
            }
            if not line.tax_repartition_line_id:
                line.compute_all_tax[frozendict({'id': line.id})] = {
                    'tax_tag_ids': [(6, 0, compute_all_currency['base_tags'])],
                }

    def _convert_to_tax_base_line_dict(self):
        """ Convert the current record to a dictionary in order to use the generic taxes computation method
        defined on account.tax.
        :return: A python dictionary.
        """
        self.ensure_one()
        is_invoice = self.move_id.is_invoice(include_receipts=True)
        sign = -1 if self.move_id.is_inbound(include_receipts=True) else 1

        return self.env['account.tax']._convert_to_tax_base_line_dict(
            self,
            partner=self.partner_id,
            currency=self.currency_id,
            product=self.product_id,
            taxes=self.tax_ids,
            price_unit=self.price_unit_with_discount if is_invoice else self.amount_currency,
            quantity=self.quantity if is_invoice else 1.0,
            discount=self.discount if is_invoice else 0.0,
            account=self.account_id,
            analytic_distribution=self.analytic_distribution,
            price_subtotal=sign * self.amount_currency,
            is_refund=self.is_refund,
            rate=(abs(self.amount_currency) / abs(self.balance)) if self.balance else 1.0
        )

    @api.ondelete(at_uninstall=False)
    def _prevent_automatic_line_deletion(self):
        if not self.env.context.get('dynamic_unlink'):
            for line in self:
                # if line.display_type == 'tax' and line.move_id.line_ids.tax_ids:
                #     raise ValidationError(_(
                #         "You cannot delete a tax line as it would impact the tax report"
                #     ))
                if line.display_type == 'payment_term':
                    raise ValidationError(_(
                        "You cannot delete a payable/receivable line as it would not be consistent "
                        "with the payment terms"
                    ))
