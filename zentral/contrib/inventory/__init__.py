from importlib import import_module
from zentral.conf import settings

__all__ = ['inventory']


inventory_settings = settings['apps']['zentral.contrib.inventory']

def get_inventory(inventory_settings):
    inventory_settings = inventory_settings.copy()
    backend = inventory_settings.pop('backend')
    module = import_module(backend)
    return getattr(module, "InventoryClient")(inventory_settings)

inventory = get_inventory(inventory_settings)


def get_api_key(inventory_settings):
    return inventory_settings.get('api_key', None)

api_key = get_api_key(inventory_settings)
