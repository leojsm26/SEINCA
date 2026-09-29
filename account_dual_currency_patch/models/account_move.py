# account_dual_currency_patch/models/account_move.py
# -*- coding: utf-8 -*-
from decimal import Decimal, ROUND_HALF_UP
import logging

from odoo import api, fields, models
from odoo.tools import float_is_zero

_logger = logging.getLogger(__name__)
ROUND_PREC = '0.01'  # 2 decimales; cambia si necesitas otra precisión


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Nota: asumimos que el módulo original ya define tax_today y los campos USD.
    # Este parche es defensivo y solo escribe campos existentes.

    def _has_field(self, record, fname):
        """True si el modelo del record tiene el campo fname (evita errores)."""
        return fname in record._fields

    def _safe_div_decimal(self, value, rate):
        """Divide value entre rate devolviendo Decimal redondeado. Evita division por 0."""
        try:
            if not rate or float_is_zero(rate, precision_digits=6):
                return Decimal('0.0')
            return (Decimal(str(value)) / Decimal(str(rate))).quantize(
                Decimal(ROUND_PREC), rounding=ROUND_HALF_UP
            )
        except Exception as e:
            _logger.exception("Error en _safe_div_decimal: %s", e)
            return Decimal('0.0')

    def _recompute_usd_amounts(self):
        """Recalcula campos USD de líneas y del move usando move.tax_today.

        - Solo escribe campos que existan en los modelos.
        - Usa contexto 'skip_usd_recompute' para evitar bucles.
        """
        for move in self:
            rate = move.tax_today or 0.0
            if not rate or float_is_zero(rate, precision_digits=6):
                _logger.warning(
                    "account_dual_currency_patch: Skip USD recompute for move %s: invalid tax_today=%s",
                    move.id, rate
                )
                continue

            _logger.info(
                "[account_dual_currency_patch] Recomputing USD for move %s using rate %s",
                move.id, rate
            )

            # Recalcular líneas: debit_usd / credit_usd / balance_usd
            line_updates = []
            for line in move.line_ids:
                debit = getattr(line, 'debit', 0.0) or 0.0
                credit = getattr(line, 'credit', 0.0) or 0.0

                debit_usd = self._safe_div_decimal(debit, rate)
                credit_usd = self._safe_div_decimal(credit, rate)
                balance_usd = (debit_usd - credit_usd).quantize(Decimal(ROUND_PREC), rounding=ROUND_HALF_UP)

                vals = {}
                if self._has_field(line, 'debit_usd'):
                    vals['debit_usd'] = float(debit_usd)
                if self._has_field(line, 'credit_usd'):
                    vals['credit_usd'] = float(credit_usd)
                if self._has_field(line, 'balance_usd'):
                    vals['balance_usd'] = float(balance_usd)

                if vals:
                    line_updates.append((line, vals))

            # Escribimos por recordset en lote con contexto que evite reentrada
            if line_updates:
                # agrupamos por recordset para hacer write en batch (más eficiente)
                lines = self.env['account.move.line'].browse([l.id for l, _ in line_updates])
                # Preparamos una sola lista de dicts: Odoo permite write sobre rs con dict individual, pero
                # aquí usamos write repetido por eficiencia/claridad.
                try:
                    # Convertimos a lista de dicts aplicables (usamos same vals para todos,
                    # pero cada línea puede requerir distintos vals; por eso iteramos)
                    for line, vals in line_updates:
                        line.with_context(skip_usd_recompute=True).write(vals)
                except Exception:
                    _logger.exception("Error escribiendo campos USD en líneas para move %s", move.id)

            # DESHABILITADO: No sobrescribir amount_*_usd ya que _amount_all_usd lo calcula correctamente
            # El método _amount_all_usd en account_dual_currency ya calcula estos valores usando la tasa
            # de la factura (no la del día), por lo que NO debemos sobrescribirlos aquí.
            # 
            # Recalcular totales del move en USD: amount_total, amount_untaxed, amount_tax
            # move_vals = {}
            # if self._has_field(move, 'amount_total_usd'):
            #     amount_total_usd = self._safe_div_decimal(move.amount_total or 0.0, rate)
            #     move_vals['amount_total_usd'] = float(amount_total_usd)
            # if self._has_field(move, 'amount_untaxed_usd'):
            #     amount_untaxed_usd = self._safe_div_decimal(move.amount_untaxed or 0.0, rate)
            #     move_vals['amount_untaxed_usd'] = float(amount_untaxed_usd)
            # if self._has_field(move, 'amount_tax_usd'):
            #     amount_tax_usd = self._safe_div_decimal(move.amount_tax or 0.0, rate)
            #     move_vals['amount_tax_usd'] = float(amount_tax_usd)

            # if move_vals:
            #     try:
            #         move.with_context(skip_usd_recompute=True).write(move_vals)
            #     except Exception:
            #         _logger.exception("Error escribiendo totales USD para move %s", move.id)

    @api.model
    def create(self, vals):
        """
        Al crear: garantizamos que el documento nazca *sin tasa* (tax_today vacio)
        y con montos USD a 0 (si esos campos existen).
        """
        # Si viene tax_today lo eliminamos: nacen sin tasa
        if 'tax_today' in vals:
            vals.pop('tax_today', None)

        # Si vienen campos USD en vals, inicializarlos a 0
        for fname in ('amount_total_usd', 'amount_untaxed_usd', 'amount_tax_usd'):
            if fname in vals:
                vals[fname] = 0.0

        move = super().create(vals)

        # Aseguramos que si por alguna razón la moneda no es VEF, no rompemos nada:
        # la política solicitada aplica a documentos creados en VEF (moneda base).
        # No hacemos recompute aquí por diseño: se hará en write cuando venga la tasa.
        _logger.debug("account_dual_currency_patch: move %s created without tax_today", move.id)
        return move

    def write(self, vals):
        """
        MODIFICADO: Solo recomputar debit_usd/credit_usd de líneas.
        NO recomputar amount_*_usd del move, ya que _amount_all_usd lo hace correctamente.
        
        Después de hacer super().write(vals) decidimos si recomputar:
        - Recompute solo para líneas (debit_usd, credit_usd) cuando:
            * se escribió tax_today en vals (nuevo valor), O
            * se modificaron line_ids (posible cambio Bs)
        - Los totales (amount_total_usd, amount_untaxed_usd, amount_tax_usd) se calculan
          automáticamente por _amount_all_usd en account_dual_currency
        - Si estamos en contexto skip_usd_recompute delegamos directo para evitar loops.
        """
        if self.env.context.get('skip_usd_recompute'):
            return super().write(vals)

        # Hacemos el write primero (necesitamos montos base actualizados)
        res = super().write(vals)

        # DESHABILITADO: No recomputar automáticamente, dejar que _amount_all_usd maneje los totales
        # Decidir si necesitamos recomputar
        # needs_recompute = False
        # trigger_keys = ('tax_today', 'line_ids', 'amount_total', 'amount_untaxed', 'amount_tax')
        # for k in trigger_keys:
        #     if k in (vals or {}):
        #         needs_recompute = True
        #         break

        # if needs_recompute:
        #     # Filtrar moves que tienen una tasa final válida
        #     moves = self.filtered(lambda m: (getattr(m, 'tax_today', False) and not float_is_zero(m.tax_today, precision_digits=6)))
        #     if moves:
        #         try:
        #             moves._recompute_usd_amounts()
        #         except Exception:
        #             _logger.exception("account_dual_currency_patch: Error recomputing USD for moves %s", moves.ids)
        return res
