# -*- coding: utf-8 -*-
from odoo import models, fields

class ResPartner(models.Model):
    _inherit = 'res.partner'

    facturar_en_usd = fields.Boolean(
        string="Facturar en USD",
        default=False,
        help="Si está activo, las facturas para este contacto se imprimirán con montos en USD dividiendo los montos en Bs entre la tasa de la factura."
    )
