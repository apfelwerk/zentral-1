from datetime import datetime
import json
from django.views import generic
from django.http import HttpResponseForbidden, JsonResponse
from zentral.contrib.osquery.models import Node
from zentral.core.stores import frontend_store
from . import inventory, api_key
from .events import post_inventory_event


class IndexView(generic.ListView):
    template_name = "inventory/machine_list.html"

    def get_queryset(self):
        machines = inventory.machines()
        machines.sort(key=lambda d: d['name'].upper())
        return machines


class MachineView(generic.TemplateView):
    template_name = "inventory/machine_detail.html"

    def get_context_data(self, **kwargs):
        context = super(MachineView, self).get_context_data(**kwargs)
        md = inventory.machine(context['serial_number'])
        context['machine'] = md
        context['links'] = md['_links']
        context['nodes'] = Node.objects.filter(enroll_secret__icontains=context['serial_number'])
        return context


class MachineEventSet(object):
    def __init__(self, machine_serial_number, event_type=None):
        self.machine_serial_number = machine_serial_number
        self.event_type = event_type
        self.store = frontend_store

    def count(self):
        return self.store.count(self.machine_serial_number, self.event_type)

    def __getitem__(self, k):
        if isinstance(k, slice):
            start = int(k.start or 0)
            stop = int(k.stop or start + 1)
        else:
            start = k
            stop = k + 1
        return self.store.fetch(self.machine_serial_number, start, stop - start, self.event_type)


class MachineEventsView(generic.ListView):
    template_name = "inventory/machine_events.html"
    paginate_by = 10

    def get_context_data(self, **kwargs):
        context = super(MachineEventsView, self).get_context_data(**kwargs)
        context['machine'] = self.machine
        page = context['page_obj']
        if page.has_next():
            qd = self.request.GET.copy()
            qd['page'] = page.next_page_number()
            context['next_url'] = "?{}".format(qd.urlencode())
        if page.has_previous():
            qd = self.request.GET.copy()
            qd['page'] = page.previous_page_number()
            context['previous_url'] = "?{}".format(qd.urlencode())
        event_types = []
        total_events = 0
        request_event_type = self.request.GET.get('event_type')
        for event_type, count in frontend_store.event_types_with_usage(self.machine['serial_number']).items():
            total_events += count
            event_types.append((event_type,
                                request_event_type == event_type,
                                "{} ({})".format(event_type.replace('_', ' ').title(), count)))
        event_types.sort()
        event_types.insert(0, ('',
                               request_event_type in [None, ''],
                               'All ({})'.format(total_events)))
        context['event_types'] = event_types
        return context

    def get_queryset(self):
        self.machine = inventory.machine(self.kwargs['serial_number'])
        et = self.request.GET.get('event_type')
        return MachineEventSet(self.machine['serial_number'], et)

class MachineAPIView(generic.View):
    def post(self, request):
        err = None
        if api_key is None:
            err = "API endpoint improperly configured. Missing API key."
        elif request.META.get('HTTP_ZENTRAL_INVENTORY_API_KEY', None) != api_key:
            err = "Missing or invalid API key in request."
        if err:
            return HttpResponseForbidden(err)
        machine_d = json.loads(request.read().decode('utf-8'))
        # set some missing elements
        machine_d['last_contact_at'] = datetime.utcnow()
        machine_d['last_report_at'] = datetime.utcnow()
        ip = self.request.META.get("HTTP_X_REAL_IP", "")
        if not machine_d.get('public_ip_address', None) and ip:
            machine_d['public_ip_address'] = ip
        # fix the osx apps attribute
        osx_apps = [[app_d['name'], app_d['version']] for app_d in machine_d.pop('osx_apps', [])]
        osx_apps.sort(key=lambda t: (t[0].upper(), t[1]))
        machine_d['osx_apps'] = osx_apps
        # sync the inventory cache
        machine_d, event_payload = inventory.sync_machine(machine_d)
        if event_payload:
            user_agent = self.request.META.get("HTTP_USER_AGENT", "")
            post_inventory_event(machine_d['serial_number'],
                                 event_payload,
                                 user_agent=user_agent,
                                 ip=ip)
        return JsonResponse(event_payload)
