from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round, float_compare
from decimal import Decimal, ROUND_HALF_UP

DECIMALS = 4
TOLERANCE_PCT = 0.03  # 3% de tolerancia para diferencias por redondeo 
 

class AccountMove(models.Model):
    _inherit = "account.move"

    approval_state = fields.Selection([
        ("none", "Sin aprobación"),
        ("waiting", "En espera de aprobación"),
        ("approved", "Aprobado"),
        ("rejected", "Rechazado")
    ], default="none", string="Estado de aprobación")

    def action_post(self):
        for move in self:
            # Saltar validación si viene del wizard o de la aprobación para evitar bucle
            if self.env.context.get("skip_price_validation"):
                return super(AccountMove, self).action_post()

            # Validaciones específicas de facturas de proveedor
            if move.move_type == "in_invoice":
                if not move.tax_today or float_round(move.tax_today, DECIMALS) == 0.0:
                    raise UserError(_("La factura no tiene tasa de cambio (tax_today) válida."))

                mayores = []
                menores = []
                tolerados = []  # líneas con diferencia > 0 y <= tolerancia

                for line in move.invoice_line_ids:
                    purchase_line = line.purchase_line_id
                    if purchase_line and purchase_line.ref_unit is not None:
                        # cálculo exacto con Decimal y quantize a 4 decimales
                        def _round4(value):
                            return float(Decimal(str(value)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))

                        precio_calculado = _round4(line.price_unit / move.tax_today)
                        ref_unit = _round4(purchase_line.ref_unit)

                        # si son exactamente iguales tras redondeo no requiere aprobación
                        if precio_calculado == ref_unit:
                            cmp = 0
                        else:
                            cmp = float_compare(precio_calculado, ref_unit, precision_digits=DECIMALS)

                        if cmp == 1:  # factura > compra
                            # aplicar tolerancia del 3% respecto al precio de compra
                            if ref_unit > 0:
                                diff = precio_calculado - ref_unit
                                threshold = ref_unit * TOLERANCE_PCT
                                if diff <= threshold:
                                    # dentro de tolerancia: NO requiere aprobación
                                    cmp = 0
                                    tolerados.append({
                                        "producto": line.product_id.display_name,
                                        "compra": format(ref_unit, f".{DECIMALS}f"),
                                        "factura": format(precio_calculado, f".{DECIMALS}f"),
                                    })
                                else:
                                    mayores.append({
                                        "line": line,
                                        "producto": line.product_id,
                                        "compra": ref_unit,
                                        "factura": precio_calculado,
                                    })
                            else:
                                # sin base para tolerancia (precio compra <= 0): mantener lógica de aprobación
                                mayores.append({
                                    "line": line,
                                    "producto": line.product_id,
                                    "compra": ref_unit,
                                    "factura": precio_calculado,
                                })
                        elif cmp == -1:  # factura < compra
                            menores.append({
                                "producto": line.product_id.display_name,
                                "compra": format(ref_unit, f".{DECIMALS}f"),
                                "factura": format(precio_calculado, f".{DECIMALS}f"),
                            })

                # --- LIMPIEZA: si ya no hay líneas con SOBREPRECIO, eliminar approvals en borrador existentes ---
                if not mayores:
                    drafts = self.env["account.move.price.approval"].search([('move_id', '=', move.id), ('state', '=', 'draft')])
                    for app in drafts:
                        still_needed = False
                        for ln in app.line_ids:
                            ml = ln.move_line_id
                            try:
                                if not ml or not ml.move_id or not ml.move_id.tax_today:
                                    continue
                                # recalcular con valores actuales de la línea
                                precio_actual = float(Decimal(str(ml.price_unit / ml.move_id.tax_today)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))
                                purchase_ref = ml.purchase_line_id.ref_unit if hasattr(ml, 'purchase_line_id') and ml.purchase_line_id else None
                                if purchase_ref is None:
                                    continue
                                purchase_ref = float(Decimal(str(purchase_ref)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))
                                if float_compare(precio_actual, purchase_ref, precision_digits=DECIMALS) == 1:
                                    # volver a validar aplicando tolerancia
                                    if purchase_ref > 0:
                                        diff = precio_actual - purchase_ref
                                        threshold = purchase_ref * TOLERANCE_PCT
                                        if diff > threshold:
                                            still_needed = True
                                            break
                                        else:
                                            # dentro de tolerancia, no se necesita aprobación
                                            continue
                                    else:
                                        # sin base para tolerancia
                                        still_needed = True
                                        break
                            except Exception:
                                # si falla la comprobación, conservar la approval por seguridad
                                still_needed = True
                                break
                        if not still_needed:
                            try:
                                app.sudo().unlink()
                            except Exception:
                                pass
                # --- FIN LIMPIEZA ---
                # Si hay precios mayores → crear solicitud de autorización, NOTIFICAR y BLOQUEAR posting
                if mayores:
                    # obtener el primer usuario DEL GRUPO de aprobación; si no hay usuarios, detener y pedir configuración
                    group = self.env.ref("purchase_REF.group_price_approval", raise_if_not_found=False)
                    if not group or not group.users:
                        raise UserError(_(
                            "No hay usuarios configurados en el grupo de 'Autorización Precio Mayor'. "
                            "Asigne al menos un usuario al grupo antes de confirmar facturas con sobreprecio."
                        ))
                    approver = group.users[0]      # user record (siempre viene del grupo)

                    approval_vals = {
                        "move_id": move.id,
                        "approver_id": approver.id,
                        "line_ids": [(0, 0, {
                            "move_line_id": m["line"].id,
                            "product_id": m["producto"].id,
                            "purchase_price": float_round(m["compra"], DECIMALS),
                            "invoice_price": float_round(m["factura"], DECIMALS),
                        }) for m in mayores],
                    }
                    approval = self.env["account.move.price.approval"].create(approval_vals)
                    # marcar factura como a revisar
                    move.approval_state = "waiting"

                    # Notificar solo al aprobador asignado (partner_id -> enteros)
                    partner = approver.partner_id
                    if partner:
                        approval.message_subscribe(partner_ids=[partner.id])
                        approval.message_post(
                            body=_("Se solicita su aprobación para la factura <b>%s</b>.") % (move.display_name,),
                            partner_ids=[partner.id],
                            subtype_xmlid="mail.mt_comment",
                        )

                    # Crear actividad sólo para el aprobador (aparece en su bandeja)
                    activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
                    if activity_type:
                        # obtener ir.model de forma segura y crear activity con sudo y date_deadline
                        model_rec = self.env["ir.model"].sudo().search([("model", "=", "account.move.price.approval")], limit=1)
                        if model_rec:
                            activity_vals = {
                                "res_model_id": model_rec.id,
                                "res_id": approval.id,
                                "user_id": approver.id,
                                "activity_type_id": activity_type.id,
                                "date_deadline": fields.Date.context_today(self),
                                "summary": _("Aprobar precio"),
                                "note": _("Revisar líneas con precio mayor que la orden de compra."),
                            }
                            try:
                                self.env["mail.activity"].sudo().create(activity_vals)
                            except Exception:
                                # no interrumpir; revisar logs si falla
                                pass

                    # Abrir la solicitud en popup y NO continuar con el post (trancar)
                    return {
                        "type": "ir.actions.act_window",
                        "res_model": "account.move.price.approval",
                        "view_mode": "form",
                        "res_id": approval.id,
                        "target": "new",
                    }

                # Si hay precios menores → mostrar wizard (dejado como estaba)
                if menores:
                    detalle = "\n".join(
                        ["- %s (compra %s / factura %s)" % (m["producto"], m["compra"], m["factura"]) for m in menores]
                    )
                    return {
                        "type": "ir.actions.act_window",
                        "name": _("Verificación de precio"),
                        "res_model": "account.invoice.price.check.wizard",
                        "view_mode": "form",
                        "target": "new",
                        "context": {
                            "default_move_id": move.id,
                            "default_message": _(
                                "Verifique el precio unitario de los siguientes productos:\n%s\nYa que se están comprando a un precio MENOR al de la orden de compra."
                            ) % detalle,
                        },
                    }

                # Si hubo diferencias toleradas (mayor pero <= 3%), registrar mensaje en el chatter
                if tolerados:
                    detalle_tol = "\n".join(
                        ["- %s (compra %s / factura %s)" % (m["producto"], m["compra"], m["factura"]) for m in tolerados]
                    )
                    pct_str = f"{int(TOLERANCE_PCT * 100)}%"
                    move.message_post(
                        body=(
                            _(
                                "Factura confirmada con valor unitario mayor al de la compra soportado por la tolerancia de %s.\n%s"
                            )
                            % (pct_str, detalle_tol)
                        )
                    )

        # Validar que no haya aprobaciones pendientes
        pending = self.env["account.move.price.approval"].search([
            ("move_id", "in", self.ids),
            ("state", "!=", "approved")
        ])
        if pending:
            raise UserError(_("No se puede validar la factura, requiere aprobación de precio mayor."))

        return super(AccountMove, self).action_post()


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    price_unit_ref = fields.Float(
        string="Precio Unitario REF",
        compute="_compute_price_ref",
        store=True,
        digits=(16, 4),
        readonly=True,
    )
    price_subtotal_ref = fields.Float(
        string="Subtotal REF",
        compute="_compute_price_ref",
        store=True,
        digits=(16, 4),
        readonly=True,
    )

    @api.depends("price_unit", "quantity", "move_id.tax_today")
    def _compute_price_ref(self):
        for line in self:
            if line.move_id and line.move_id.tax_today:
                tasa = line.move_id.tax_today
                line.price_unit_ref = line.price_unit / tasa if tasa else 0.0
                line.price_subtotal_ref = line.price_unit_ref * line.quantity
            else:
                line.price_unit_ref = 0.0
                line.price_subtotal_ref = 0.0


class AccountMovePriceApproval(models.Model):
    _name = "account.move.price.approval"
    _description = "Autorización de precio mayor a compra"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    move_id = fields.Many2one("account.move", string="Factura", required=True, ondelete="cascade")
    approver_id = fields.Many2one("res.users", string="Aprobador", required=True, default=lambda self: self._default_approver())
    line_ids = fields.One2many("account.move.price.approval.line", "approval_id", string="Detalles")
    state = fields.Selection([
        ("draft", "Pendiente"),
        ("approved", "Aprobado"),
        ("rejected", "Rechazado")
    ], default="draft", tracking=True)

    # campo para controlar visibilidad del botón "Enviar" (solo visible si el usuario NO pertenece al grupo de aprobadores)
    can_send = fields.Boolean(string="Puede enviar", compute="_compute_can_send", store=False)

    @api.model
    def _default_approver(self):
        grp = self.env.ref("purchase_REF.group_price_approval", raise_if_not_found=False)
        if grp and grp.users:
            return grp.users[0].id
        return False

    def _compute_can_send(self):
        can = not self.env.user.has_group('purchase_REF.group_price_approval')
        for rec in self:
            rec.can_send = can

    def _mark_activities_done(self, model, res_id, summary_contains=None, user_id=False):
        """
        Marcar como hechas actividades relacionadas con (model,res_id).
        Opcional: filtrar por texto en summary y por user_id.
        """
        domain = [('res_model', '=', model), ('res_id', '=', res_id)]
        if user_id:
            domain.append(('user_id', '=', user_id))
        acts = self.env['mail.activity'].sudo().search(domain)
        if summary_contains:
            acts = acts.filtered(lambda a: summary_contains.lower() in (a.summary or "").lower())
        if acts:
            try:
                acts.sudo().action_done()
            except Exception:
                try:
                    acts.sudo().write({'state': 'done'})
                except Exception:
                    pass

    def action_send(self):
        """
        Enviar la solicitud al aprobador:
        - subscribir follower y publicar mensaje en la solicitud (chatter)
        - subscribir follower y publicar mensaje en la factura (chatter)
        - crear actividad To Do en la solicitud y en la factura
        - enviar mail inmediato si partner.email está configurado
        """
        for rec in self:
            approver = rec.approver_id
            if not approver:
                raise UserError(_("No hay aprobador asignado para esta solicitud."))

            partner = approver.partner_id
            if partner:
                rec.message_subscribe(partner_ids=[partner.id])
                rec.message_post(
                    body=_("Se solicita su revisión y aprobación para la factura <b>%s</b>.") % (rec.move_id.display_name,),
                    partner_ids=[partner.id],
                    subtype_xmlid="mail.mt_comment",
                )
                try:
                    rec.move_id.message_subscribe(partner_ids=[partner.id])
                    rec.move_id.message_post(
                        body=_("Se solicita su revisión y aprobación para esta factura desde la solicitud de precio mayor."),
                        partner_ids=[partner.id],
                        subtype_xmlid="mail.mt_comment",
                    )
                except Exception:
                    pass

            activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
            if activity_type:
                model_rec = self.env["ir.model"].sudo().search([("model", "=", "account.move.price.approval")], limit=1)
                if model_rec:
                    activity_vals = {
                        "res_model_id": model_rec.id,
                        "res_id": rec.id,
                        "user_id": approver.id,
                        "activity_type_id": activity_type.id,
                        "date_deadline": fields.Date.context_today(self),
                        "summary": _("Aprobar precio"),
                        "note": _("Revisar líneas con precio mayor que la orden de compra."),
                    }
                    try:
                        self.env["mail.activity"].sudo().create(activity_vals)
                    except Exception:
                        pass

            if activity_type:
                model_move = self.env["ir.model"].sudo().search([("model", "=", "account.move")], limit=1)
                if model_move:
                    activity_vals_move = {
                        "res_model_id": model_move.id,
                        "res_id": rec.move_id.id,
                        "user_id": approver.id,
                        "activity_type_id": activity_type.id,
                        "date_deadline": fields.Date.context_today(self),
                        "summary": _("Revisar y aprobar factura"),
                        "note": _("Factura requiere aprobación por precio mayor que la OC."),
                    }
                    try:
                        self.env["mail.activity"].sudo().create(activity_vals_move)
                    except Exception:
                        pass

            if partner and partner.email:
                mail_vals = {
                    "subject": _("Solicitud de aprobación de precio: %s") % rec.move_id.display_name,
                    "body_html": "<p>%s</p>" % (_("Se solicita su revisión y aprobación para la factura <b>%s</b>.") % rec.move_id.display_name),
                    "email_to": partner.email,
                    "auto_delete": True,
                }
                mail = self.env["mail.mail"].sudo().create(mail_vals)
                try:
                    mail.sudo().send(raise_exception=False)
                except Exception:
                    pass
        return True

    def action_approve(self):
        for rec in self:
            if self.env.user != rec.approver_id and not self.env.user.has_group('purchase_REF.group_price_approval'):
                raise UserError(_("No tiene permisos para aprobar esta solicitud."))
            rec.state = "approved"
            # permitir publicar la factura saltando la validación
            rec.move_id.with_context(skip_price_validation=True).action_post()
            rec.move_id.approval_state = "approved"
            # dejar comentario en la factura
            rec.move_id.message_post(body=_("Precio de factura aprobado con diferencia con respecto a la orden de compra"))
            # marcar actividades relacionadas como hechas (tanto en la solicitud como en la factura)
            self._mark_activities_done('account.move.price.approval', rec.id, summary_contains='aprobar', user_id=rec.approver_id.id)
            self._mark_activities_done('account.move', rec.move_id.id, summary_contains='revisar', user_id=rec.approver_id.id)
            rec.message_post(body=_("Solicitud aprobada por %s" % self.env.user.display_name))
        return True

    def action_reject(self):
        for rec in self:
            if self.env.user != rec.approver_id and not self.env.user.has_group('purchase_REF.group_price_approval'):
                raise UserError(_("No tiene permisos para rechazar esta solicitud."))
            rec.state = "rejected"
            rec.move_id.approval_state = "rejected"
            # dejar comentario en factura
            rec.move_id.message_post(body=_("Factura de Proveedor no aprobada por diferencia en precios con orden de compra"))
            # cancelar la factura (intentar robustamente)
            try:
                # si está publicada, pasar a borrador y luego cancelar
                if rec.move_id.state == 'posted':
                    rec.move_id.button_draft()
                rec.move_id.button_cancel()
            except Exception:
                try:
                    rec.move_id.sudo().button_cancel()
                except Exception:
                    pass
            # marcar actividades relacionadas como hechas
            self._mark_activities_done('account.move.price.approval', rec.id, summary_contains='aprobar', user_id=rec.approver_id.id)
            self._mark_activities_done('account.move', rec.move_id.id, summary_contains='revisar', user_id=rec.approver_id.id)
        return True


class AccountMovePriceApprovalLine(models.Model):
    _name = "account.move.price.approval.line"
    _description = "Detalle de línea con precio mayor"

    approval_id = fields.Many2one("account.move.price.approval", string="Autorización", required=True, ondelete="cascade")
    move_line_id = fields.Many2one("account.move.line", string="Línea de factura", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", related="move_line_id.product_id", store=True)
    purchase_price = fields.Float(string="Precio en OC", digits=(16, 4))
    invoice_price = fields.Float(string="Precio en Factura", digits=(16, 4))


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    move_type = fields.Selection(related='move_id.move_type', store=False)
