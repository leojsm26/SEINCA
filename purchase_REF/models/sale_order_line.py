from odoo import api, fields, models
class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # Campo existente que ya definimos para ref sobre price_subtotal:
    ref = fields.Float(
        string='REF Subtotal',
        compute='_compute_ref',
        store=True,
        readonly=True,
        currency_field='order_currency_ref_id',
        digits=(16, 4)
     )
    # Campo auxiliar que indica en qué moneda formatear "ref" y "ref_unit"
    order_currency_ref_id = fields.Many2one(
        'res.currency',
        string="Moneda REF",
        compute='_compute_ref_currency',
        store=True,
    )

    # Nuevo campo que hace referencia al precio unitario convertido:
    ref_unit = fields.Float(
        string='REF Unit',
        compute='_compute_ref_unit',
        inverse='_inverse_ref_unit',
        store=True,
        currency_field='order_currency_ref_id',
        digits=(16, 4)
    )

    @api.depends('order_id.currency_id', 'order_id.company_id.currency_id')
    def _compute_ref_currency(self):
        """
        Definimos que 'order_currency_ref_id' sea la moneda CONTRARIA a la de la orden:
        - Si la orden está en USD → queremos mostrar REF en VEF (moneda de compañía).
        - Si la orden está en VEF → queremos mostrar REF en USD.
        - Para cualquier otra moneda → mantendremos USD por defecto.
        """
        usd = self.env.ref('base.USD', raise_if_not_found=False)
        for line in self:
            order = line.order_id
            if not order:
                line.order_currency_ref_id = False
                continue

            order_cur = order.currency_id         # moneda de la orden
            comp_cur = order.company_id.currency_id  # moneda de la compañía

            if usd and order_cur == usd:
                # Orden en USD → REF en VEF
                line.order_currency_ref_id = comp_cur
            elif comp_cur and order_cur == comp_cur and usd:
                # Orden en VEF → REF en USD
                line.order_currency_ref_id = usd
            else:
                # Cualquier otro caso, usamos la moneda de compañía o la de la orden
                line.order_currency_ref_id = comp_cur or order_cur

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override create para forzar recálculo de ref y ref_unit en imports masivos.
        """
        lines = super().create(vals_list)
        
        # Forzar flush para asegurar que price_subtotal se compute
        lines.flush_model()
        
        # Invalidar caché y recomputar los campos REF
        lines.invalidate_recordset(['price_subtotal'])
        lines._compute_ref_currency()
        lines._compute_ref()
        lines._compute_ref_unit()
        
        return lines

    def write(self, vals):
        """
        Override write para recomputar ref/ref_unit cuando cambian campos relevantes.
        """
        res = super().write(vals)
        
        # Detectar si hubo cambios en campos que afectan el cálculo
        trigger_fields = {
            'price_unit', 'product_uom_qty', 'discount', 'tax_id', 
            'order_id', 'product_id'
        }
        
        if set(vals.keys()) & trigger_fields:
            # Forzar flush y recálculo
            self.flush_model()
            self.invalidate_recordset(['price_subtotal'])
            self._compute_ref_currency()
            self._compute_ref()
            self._compute_ref_unit()
        
        return res

    @api.onchange('price_unit', 'product_uom_qty', 'discount', 'tax_id', 'order_id', 'product_id')
    def _onchange_ref(self):
        """
        Onchange para actualizar ref y ref_unit en tiempo real en la UI.
        """
        for line in self:
            # Forzar recálculo de moneda REF si es necesario
            if not line.order_currency_ref_id:
                line._compute_ref_currency()
            
            # Recalcular ambos campos
            line._compute_ref_unit()
            line._compute_ref()

    @api.depends('price_subtotal', 'order_id.currency_id', 'order_id.company_id.currency_id', 'order_id.date_order')
    def _compute_ref(self):
        """
        Cálculo de REF Subtotal ($):

        - Si la orden está en USD, price_subtotal viene en USD;
        REF = USD * tasa_USD→VEF
        - Si la orden está en VEF, price_subtotal viene en VEF;
        REF = VEF / tasa_USD→VEF
        - Otros casos → REF = 0.0
        """
        for line in self:
            # valor por defecto
            line.ref = 0.0
            order = line.order_id
            target_currency = line.order_currency_ref_id
            if not order or not target_currency:
                continue

            price = line.price_subtotal or 0.0
            if not price:
                continue

            order_currency = order.currency_id
            company = order.company_id
            date_order = order.date_order or fields.Date.context_today(order)

            try:
                ref_value = order_currency._convert(
                    price,
                    target_currency,
                    company,
                    date_order,
                )
            except Exception:
                ref_value = 0.0

            line.ref = target_currency.round(ref_value) if target_currency else ref_value

    @api.depends('price_unit', 'order_id.currency_id', 'order_id.company_id.currency_id', 'order_id.date_order')
    def _compute_ref_unit(self):
        """
        Cálculo de REF Unit ($):

        Lógica idéntica a _compute_ref, pero aplicada a price_unit:
        - Si la orden está en USD, price_unit viene en USD;
          REF Unit = USD * tasa_USD→VEF
        - Si la orden está en VEF, price_unit viene en VEF;
          REF Unit = VEF / tasa_USD→VEF
        - Otros casos → REF Unit = 0.0
        """
        for line in self:
            line.ref_unit = 0.0
            order = line.order_id
            target_currency = line.order_currency_ref_id
            if not order or not target_currency:
                continue

            price_u = line.price_unit
            if not price_u:
                continue

            order_currency = order.currency_id
            company = order.company_id
            date_order = order.date_order or fields.Date.context_today(order)

            try:
                refu = order_currency._convert(
                    price_u,
                    target_currency,
                    company,
                    date_order,
                )
            except Exception:
                refu = 0.0

            line.ref_unit = target_currency.round(refu) if target_currency else refu

    def _inverse_ref_unit(self):
        for line in self:
            order = line.order_id
            target_currency = line.order_currency_ref_id
            if not order or not target_currency:
                continue

            ref_unit = line.ref_unit
            if ref_unit in (False, None):
                continue

            order_currency = order.currency_id
            company = order.company_id
            date_order = order.date_order or fields.Date.context_today(order)

            try:
                price_unit = target_currency._convert(
                    ref_unit,
                    order_currency,
                    company,
                    date_order,
                )
            except Exception:
                continue

            line.price_unit = order_currency.round(price_unit) if order_currency else price_unit


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)

        usd = self.env.ref('base.USD', raise_if_not_found=False)
        for line in lines:
            move = line.move_id
            if not move or move.move_type != 'out_invoice':
                continue

            company = move.company_id
            company_currency = company.currency_id
            if move.currency_id != company_currency:
                continue

            sale_line = False
            if line.sale_line_ids:
                sale_line = line.sale_line_ids[:1]
            elif getattr(line, 'sale_line_id', False):
                sale_line = line.sale_line_id

            if not sale_line:
                continue

            ref_unit_usd = sale_line.ref_unit or 0.0
            if not ref_unit_usd:
                continue

            if getattr(move, 'tax_today', False):
                rate = move.tax_today
            else:
                inv_date = move.invoice_date or move.date or fields.Date.context_today(self)
                if not usd:
                    continue
                rate = usd._get_conversion_rate(
                    usd,
                    company_currency,
                    company,
                    inv_date,
                )

            if not rate:
                continue

            new_price_unit = move.currency_id.round(ref_unit_usd * rate)
            line.with_context(check_move_validity=False).write({'price_unit': new_price_unit})

        return lines