from zentral.core.events import BaseEvent, EventMetadata, EventRequest, register_event_type


class InventoryUpdateEvent(BaseEvent):
    event_type = "inventory_update"

register_event_type(InventoryUpdateEvent)


def post_inventory_event(msn, data, uuid=None, index=None, user_agent=None, ip=None):
    event_cls = InventoryUpdateEvent
    if user_agent or ip:
        request = EventRequest(user_agent, ip)
    else:
        request = None
    metadata = EventMetadata(event_cls.event_type,
                             machine_serial_number=msn,
                             uuid=uuid,
                             index=index,
                             request=request)
    event = event_cls(metadata, data)
    event.post()
