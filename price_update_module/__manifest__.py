{
    'name': 'Price Update Module',
    'version': '17.0.1.0.0',
    'summary': 'Actualiza la lista de precios en base a la tasa.',
    'author': 'Tu Nombre',
    'depends': ['base', 'product'],  # Asegúrate de incluir 'product'
    'data': [
        'data/cron_job.xml',
        'views/product_pricelist_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,  # Asegúrate de que no se instale automáticamente
}
