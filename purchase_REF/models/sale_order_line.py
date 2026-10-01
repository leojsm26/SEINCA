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
        Override create para evitar que el inverse de ref_unit sobreescriba price_unit al guardar.
        """
        clean_vals_list = []
        for vals in vals_list:
            vals_copy = dict(vals)
            # Si se envía price_unit de forma explícita, damos prioridad a price_unit
            if 'price_unit' in vals_copy and 'ref_unit' in vals_copy:
                vals_copy.pop('ref_unit', None)
            clean_vals_list.append(vals_copy)

        lines = super(SaleOrderLine, self.with_context(skip_ref_unit_inverse=True)).create(clean_vals_list)
        return lines

    def write(self, vals):
        """
        Override write para evitar que el inverse de ref_unit sobreescriba price_unit al guardar.
        """
        if 'price_unit' in vals:
            vals_clean = dict(vals)
            vals_clean.pop('ref_unit', None)
            return super(SaleOrderLine, self.with_context(skip_ref_unit_inverse=True)).write(vals_clean)

        return super().write(vals)

    @api.onchange('price_unit', 'product_uom_qty', 'discount', 'tax_id', 'order_id', 'product_id')
    def _onchange_ref(self):
        """
        Onchange para actualizar ref y ref_unit en tiempo real en la UI cuando cambia price_unit.
        """
        for line in self:
            if not line.order_currency_ref_id:
                line._compute_ref_currency()
            line._compute_ref_unit()
            line._compute_ref()

    @api.onchange('ref_unit')
    def _onchange_ref_unit(self):
        """
        Onchange cuando el usuario edita directamente REF Unit en la interfaz.
        Calcula el price_unit equivalente sin provocar rebotes.
        """
        for line in self:
            if line.ref_unit in (False, None):
                continue
            order = line.order_id
            target_currency = line.order_currency_ref_id
            if not order or not target_currency:
                continue

            order_currency = order.currency_id
            company = order.company_id
            date_order = order.date_order or fields.Date.context_today(order)

            try:
                # Comprobar si el price_unit actual ya genera este ref_unit
                current_ref = order_currency._convert(
                    line.price_unit or 0.0,
                    target_currency,
                    company,
                    date_order,
                )
                if abs(current_ref - line.ref_unit) < 0.0001:
                    continue

                new_price_unit = target_currency._convert(
                    line.ref_unit,
                    order_currency,
                    company,
                    date_order,
                )
                line.price_unit = order_currency.round(new_price_unit) if order_currency else new_price_unit
                line._compute_ref()
            except Exception:
                pass

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
        Mantiene 4 decimales de precisión según definición del campo.
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

            line.ref_unit = round(refu, 4) if refu else 0.0

    def _inverse_ref_unit(self):
        """
        Inverse para actualizar price_unit solo cuando el usuario cambia ref_unit.
        Protegido contra ciclos de redondeo.
        """
        if self.env.context.get('skip_ref_unit_inverse'):
            return

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
                # Comprobar si el price_unit actual ya equivale a este ref_unit
                current_ref = order_currency._convert(
                    line.price_unit or 0.0,
                    target_currency,
                    company,
                    date_order,
                )
                if abs(current_ref - ref_unit) < 0.0001:
                    continue

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