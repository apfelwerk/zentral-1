import json
import logging
from dateutil import parser
from django.core.urlresolvers import reverse_lazy
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.generic import View, DetailView, ListView, TemplateView
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from zentral.contrib.inventory.models import MachineSnapshot
from zentral.core.probes.views import BaseProbeView
from zentral.core.stores import stores
from zentral.utils.api_views import (JSONPostAPIView, verify_secret, APIAuthError,
                                     BaseEnrollmentView, BaseInstallerPackageView)
from zentral.utils.sql import format_sql
from . import osquery_conf, event_type_probes, probes, DEFAULT_ZENTRAL_INVENTORY_QUERY
from .events import post_enrollment_event, post_request_event, post_events_from_osquery_log
from .forms import DistributedQueryForm
from .models import enroll, DistributedQuery, DistributedQueryNode
from .osx_package.builder import OsqueryZentralEnrollPkgBuilder

logger = logging.getLogger('zentral.contrib.osquery.views')


class ProbesView(TemplateView):
    template_name = "osquery/probes.html"

    def get_context_data(self, **kwargs):
        context = super(ProbesView, self).get_context_data(**kwargs)
        context['osquery'] = True
        context['probes'] = probes
        context['event_type_probes'] = event_type_probes
        return context


class EnrollmentView(BaseEnrollmentView):
    template_name = "osquery/enrollment.html"
    section = "osquery"


class InstallerPackageView(BaseInstallerPackageView):
    builder = OsqueryZentralEnrollPkgBuilder
    module = "zentral.contrib.osquery"


class ProbeView(BaseProbeView):
    template_name = "osquery/probe.html"
    section = "osquery"

    def get_extra_context_data(self, probe):
        # queries
        schedule = []
        for idx, osquery in enumerate(probe.get('osquery', {}).get('schedule', [])):
            # query links. match query_name.
            osquery_ctx = {}
            query_links = []
            query_name = "{}_{}".format(probe['name'], idx)
            for store in stores:
                url = store.get_visu_url({'name': [query_name]})
                if url:
                    query_links.append((store.name, url))
            query_links.sort()
            osquery_ctx['links'] = query_links
            osquery_ctx['html_query'] = format_sql(osquery['query'])
            osquery_ctx['interval'] = osquery['interval']
            osquery_ctx['value'] = osquery.get('value', None)
            osquery_ctx['description'] = osquery.get('description', None)
            schedule.append(osquery_ctx)

        # probe links. query name starts with probe name.
        probe_links = []
        for store in stores:
            url = store.get_visu_url({'name__startswith': [probe['name']]})
            if url:
                probe_links.append((store.name, url))
        probe_links.sort()

        return {'osquery_schedule': schedule,
                'osquery_file_paths': probe.get('osquery', {}).get('file_paths', {}),
                'probe_links': probe_links}


class DistributedIndexView(ListView):
    model = DistributedQuery

    def get_context_data(self, **kwargs):
        ctx = super(DistributedIndexView, self).get_context_data(**kwargs)
        ctx['osquery'] = True
        return ctx


class CreateDistributedView(CreateView):
    model = DistributedQuery
    form_class = DistributedQueryForm

    def get_context_data(self, **kwargs):
        ctx = super(CreateDistributedView, self).get_context_data(**kwargs)
        ctx['osquery'] = True
        return ctx


class DistributedView(DetailView):
    model = DistributedQuery

    def get_context_data(self, **kwargs):
        ctx = super(DistributedView, self).get_context_data(**kwargs)
        ctx['osquery'] = True
        return ctx


class UpdateDistributedView(UpdateView):
    model = DistributedQuery
    form_class = DistributedQueryForm

    def get_context_data(self, **kwargs):
        ctx = super(UpdateDistributedView, self).get_context_data(**kwargs)
        ctx['osquery'] = True
        return ctx


class DeleteDistributedView(DeleteView):
    model = DistributedQuery
    success_url = reverse_lazy('osquery:distributed_index')

    def get_context_data(self, **kwargs):
        ctx = super(DeleteDistributedView, self).get_context_data(**kwargs)
        ctx['osquery'] = True
        return ctx


class DownloadDistributedView(View):
    def get(self, request, *args, **kwargs):
        dq = get_object_or_404(DistributedQuery, pk=kwargs['pk'])
        return JsonResponse(dq.serialize())


# API


class EnrollView(JSONPostAPIView):
    def check_data_secret(self, data):
        data = verify_secret(data['enroll_secret'], "zentral.contrib.osquery")
        self.machine_serial_number = data['machine_serial_number']
        self.business_unit = data.get('business_unit', None)

    def do_post(self, data):
        ms, action = enroll(self.machine_serial_number,
                            self.business_unit)
        post_enrollment_event(ms.machine.serial_number,
                              self.user_agent, self.ip,
                              {'action': action})
        return {'node_key': ms.reference}


class BaseNodeView(JSONPostAPIView):
    def check_data_secret(self, data):
        auth_err = None
        try:
            self.ms = MachineSnapshot.objects.current().get(source__module='zentral.contrib.osquery',
                                                            reference=data['node_key'])
        except KeyError:
            auth_err = "Missing node_key"
        except MachineSnapshot.DoesNotExist:
            auth_err = "Wrong node_key"
        if auth_err:
            logger.error("APIAuthError %s", auth_err, extra=data)
            raise APIAuthError(auth_err)
        # TODO: Better verification ?
        self.machine_serial_number = self.ms.machine.serial_number
        self.business_unit = self.ms.business_unit

    def do_post(self, data):
        post_request_event(self.machine_serial_number,
                           self.user_agent, self.ip,
                           self.request_type)
        return self.do_node_post(data)


class ConfigView(BaseNodeView):
    request_type = "config"

    def do_node_post(self, data):
        return osquery_conf


class DistributedReadView(BaseNodeView):
    request_type = "distributed_read"

    def do_node_post(self, data):
        queries = {}
        if self.machine_serial_number:
            for dqn in DistributedQueryNode.objects.new_queries_with_serial_number(self.machine_serial_number):
                dq = dqn.distributed_query
                queries['q_{}'.format(dq.id)] = dq.query
        return {'queries': queries}


class DistributedWriteView(BaseNodeView):
    request_type = "distributed_write"

    def do_node_post(self, data):
        for key, val in data.get('queries').items():
            dq_id = int(key.rsplit('_', 1)[-1])
            sn = self.machine_serial_number
            try:
                dqn = DistributedQueryNode.objects.get(distributed_query__id=dq_id,
                                                       machine_serial_number=sn)
            except DistributedQueryNode.DoesNotExist:
                logger.error("Unknown distributed query node query %s sn %s", dq_id, sn)
            else:
                dqn.set_json_result(val)
        return {}


class LogView(BaseNodeView):
    request_type = "log"

    def do_node_post(self, data):
        inventory_results = []
        other_results = []
        data_data = data.pop('data')
        if not isinstance(data_data, list):
            # TODO verify. New since osquery 1.6.4 ?
            data_data = [json.loads(data_data)]
        for r in data_data:
            if r.get('name', None) == DEFAULT_ZENTRAL_INVENTORY_QUERY:
                inventory_results.append((parser.parse(r['calendarTime']), r['snapshot']))
            else:
                other_results.append(r)
        data['data'] = other_results
        if inventory_results:
            inventory_results.sort(reverse=True)
            last_snapshot = inventory_results[0][1]
            tree = {'source': {'module': self.ms.source.module,
                               'name': self.ms.source.name},
                    'machine': {'serial_number': self.machine_serial_number},
                    'reference': self.ms.reference}
            if self.business_unit:
                tree['business_unit'] = self.business_unit.serialize()
            for t in last_snapshot:
                table_name = t.pop('table_name')
                if table_name == 'os_version':
                    tree['os_version'] = t
                elif table_name == 'system_info':
                    tree['system_info'] = t
            try:
                MachineSnapshot.objects.commit(tree)
            except:
                logger.exception('Cannot save machine snapshot')
        post_events_from_osquery_log(self.machine_serial_number,
                                     self.user_agent, self.ip, data)
        return {}
