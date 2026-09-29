# -*- coding: utf-8 -*-

from odoo import models, fields, _

class AccountMoveReversalInherit(models.TransientModel):
    _inherit = 'account.move.reversal'

    supplier_invoice_number = fields.Char(
        string='Número de factura del proveedor', size=64, store=True)

    # Asegúrate de que el campo move_id esté bien definido
    move_id = fields.Many2one('account.move', string='Factura Original')

    # Definición indirecta del campo 'correlative'
    correlative = fields.Char(
        related='move_id.correlative', string="Número de Control", store=True,
        help="Número utilizado para gestionar facturas preimpresas por ley")

    def _prepare_default_reversal(self, move):
        default_values = super()._prepare_default_reversal(move)
        if move.move_type in ('out_invoice', 'in_invoice', 'out_refund', 'in_refund'):
            fecha_orig = move._fecha_para_tax_today() if hasattr(move, '_fecha_para_tax_today') else (move.invoice_date or move.date)
            tasa_orig = getattr(move, 'tax_today', False) or (hasattr(move, '_get_tasa_usd_by_date') and move._get_tasa_usd_by_date(fecha_orig, move.company_id))
            if tasa_orig:
                default_values['tax_today'] = tasa_orig
                default_values['tax_today_edited'] = True
            if getattr(move, 'currency_id', False):
                default_values['currency_id'] = move.currency_id.id
            if hasattr(move, 'trm_invoice'):
                default_values['trm_invoice'] = True
            default_values['reversed_entry_id'] = move.id
        return default_values

    def reverse_moves(self, is_modify=False):
        moves = self.env['account.move'].browse(self.env.context['active_ids']) if self.env.context.get('active_model') == 'account.move' else self.move_id

        # Crear valores por defecto para la reversión
        default_values_list = []
        for move in moves:
            default_values_list.append(self._prepare_default_reversal(move))

        batches = [
            [self.env['account.move'], [], True],   # Movimientos que serán cancelados por las reversas.
            [self.env['account.move'], [], False],  # Otros movimientos.
        ]
        for move, default_vals in zip(moves, default_values_list):
            is_auto_post = bool(default_vals.get('auto_post'))
            is_cancel_needed = not is_auto_post and (is_modify or self.move_type == 'entry')
            batch_index = 0 if is_cancel_needed else 1
            batches[batch_index][0] |= move
            batches[batch_index][1].append(default_vals)

        # Manejo del método de reversión
        moves_to_redirect = self.env['account.move']
        for moves, default_values_list, is_cancel_needed in batches:
            if default_values_list:
                for d_vals in default_values_list:
                    if self.correlative and not d_vals.get('correlative'):
                        d_vals['correlative'] = self.correlative
                    if self.supplier_invoice_number and not d_vals.get('supplier_invoice_number'):
                        d_vals['supplier_invoice_number'] = self.supplier_invoice_number

            new_moves = moves._reverse_moves(default_values_list, cancel=is_cancel_needed)

            for new_move in new_moves:
                if new_move.move_type in ('out_refund', 'in_refund') and new_move.reversed_entry_id:
                    orig = new_move.reversed_entry_id
                    fecha_orig = orig._fecha_para_tax_today() if hasattr(orig, '_fecha_para_tax_today') else (orig.invoice_date or orig.date)
                    tasa_orig = orig.tax_today or (hasattr(orig, '_get_tasa_usd_by_date') and orig._get_tasa_usd_by_date(fecha_orig, orig.company_id))
                    if tasa_orig:
                        new_move.with_context(skip_tax_today_update=True).write({
                            'tax_today': tasa_orig,
                            'tax_today_edited': True,
                        })
                    if orig.currency_id and new_move.currency_id != orig.currency_id:
                        new_move.currency_id = orig.currency_id
                    if hasattr(new_move, 'trm_invoice'):
                        new_move.trm_invoice = True
                    if hasattr(new_move, 'action_recalcular_campos_duales'):
                        new_move.action_recalcular_campos_duales()

            if new_moves.state != 'draft':
                new_moves.already_posted_iva()

            if is_modify:
                moves_vals_list = []
                for move in moves.with_context(include_business_fields=True):
                    moves_vals_list.append(move.copy_data({'date': self.date or move.date})[0])
                new_moves = self.env['account.move'].create(moves_vals_list)

            moves_to_redirect |= new_moves

        # Crear acción
        action = {
            'name': _('Reverse Moves'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
        }
        if len(moves_to_redirect) == 1:
            action.update({
                'view_mode': 'form',
                'res_id': moves_to_redirect.id,
            })
        else:
            action.update({
                'view_mode': 'tree,form',
                'domain': [('id', 'in', moves_to_redirect.ids)],
            })
        return action
