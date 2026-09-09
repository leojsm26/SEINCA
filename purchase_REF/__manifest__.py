{
    'name': 'REF en línea de compra',
    'version': '17.0.1.0.0',
    'depends': ['purchase', 'sale_management', 'account', 'payment', 'mail'],
    'author': 'Ing. Gabriel Sirit / Contables',
    'category': 'Purchases',
    'description': 'Agrega el campo REF para conversión de Bs a $ en la línea de compra y autorización de precios mayores.',
    'data': [
        'security/security.xml',             # primero los grupos
        'security/ir.model.access.csv',      # luego los permisos
        'views/account_invoice_price_check_wizard.xml',
        'views/account_move_price_approval_view.xml',
    ],
    'installable': True,
    'application': False,
}