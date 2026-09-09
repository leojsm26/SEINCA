from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AccountInvoicePriceCheckWizard(models.TransientModel):
    _name = "account.invoice.price.check.wizard"
    _description = "Confirmación diferencia de precio factura vs compra"

    move_id = fields.Many2one("account.move", string="Factura", required=True)
    message = fields.Text("Mensaje", readonly=True)

    def action_confirm(self):
        """Forzar la publicación y registrar en chatter"""
        self.ensure_one()
        if not self.move_id:
            raise UserError(_("No se encontró la factura."))

        # Registrar en el chatter
        self.move_id.message_post(
            body=_(
                "Se confirmó la factura con una diferencia de precio entre orden de compra y factura.<br/><br/>Detalle:<br/>%s"
            ) % self.message.replace("\n", "<br/>")
        )

        # Confirmar factura saltando validación
        self.move_id.with_context(skip_price_validation=True).action_post()

        # Volver a abrir la factura
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.move_id.id,
            "target": "current",
        }


    def action_cancel(self):
        """Cerrar wizard sin publicar"""
        return {"type": "ir.actions.act_window_close"}