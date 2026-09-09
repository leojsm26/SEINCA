# -*- coding: utf-8 -*-
from odoo import api, models, fields
import logging
 
_logger = logging.getLogger(__name__)


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _get_last_purchase_price(self):
        """Obtiene el precio unitario de la última compra confirmada para este producto y proveedor"""
        self.ensure_one()
        product = self.product_id
        partner = self.order_id.partner_id

        lines = self.env['purchase.order.line'].search([
            ('product_id', '=', product.id),
            ('order_id.partner_id', '=', partner.id),
            ('order_id.state', 'in', ['purchase', 'done']),  # solo pedidos confirmados
        ], limit=50)  # buscamos varias y luego filtramos

        if not lines:
            return 0.0

        # Ordenar en Python por fecha de orden descendente
        last_line = max(lines, key=lambda l: l.order_id.date_order or fields.Datetime.from_string("1970-01-01"))

        return last_line.ref_unit if last_line else 0.0


    def _apply_ref_price(self):
        for line in self:
            if line.product_id:
                last_price = line._get_last_purchase_price()
                if last_price:
                    try:
                        tasa = float(line.order_id.tax_today or 1.0)
                    except Exception:
                        tasa = 1.0
                    # guardamos el último precio en ref_unit (USD) y calculamos el price_unit (Bs)
                    line.ref_unit = last_price
                    line.price_unit = last_price * tasa

                    _logger.info(
                        "[purchase_ref_patch] PO=%s, Prod=%s, ref_unit=%s, tasa=%s => price_unit=%s",
                        line.order_id.name, line.product_id.display_name, last_price, tasa, line.price_unit
                    )

    # --- Al seleccionar producto/UdM/cantidad en el formulario ---
    @api.onchange('product_id', 'product_uom', 'product_qty')
    def _onchange_force_ref_price(self):
        if self.product_id:
            self._apply_ref_price()


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def write(self, vals):
        res = super().write(vals)
        if 'tax_today' in vals:
            for order in self:
                order.order_line._apply_ref_price()
        return res
