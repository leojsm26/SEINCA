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
                # Usamos 'correlative' desde el modelo relacionado de 'account.move'
                default_values_list[0]['correlative'] = self.correlative
                default_values_list[0]['supplier_invoice_number'] = self.supplier_invoice_number

            new_moves = moves._reverse_moves(default_values_list, cancel=is_cancel_needed)
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
