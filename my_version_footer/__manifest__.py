{
    'name': 'Version Footer in Config',
    'version': '17.0',
    'category': 'Tools',
    'summary': 'Adds version information in the footer of res.config',
    'description': """
    This module adds a footer in the configuration settings view
    showing the current version of Odoo.
    """,
    'author': 'Tu nombre o empresa',
    'depends': ['base', 'base_setup'],
    'data': [
        'views/res_config_views.xml',
    ],
    'installable': True,
    'application': False,
}
