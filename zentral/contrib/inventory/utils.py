from django.db import connection
from zentral.conf import settings
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway


def osx_app_count():
    query = """
    select a.bundle_name as name, a.bundle_version_str as version_str, s.id as source_id, s.module as source_module,
    sum((ms.mt_next_id is null and ms.id is not null)::integer) as count
    from inventory_osxapp as a
    join inventory_osxappinstance as ai on (ai.app_id = a.id)
    join inventory_machinesnapshot_osx_app_instances as msai on (msai.osxappinstance_id = ai.id)
    join inventory_machinesnapshot as ms on (ms.id = msai.machinesnapshot_id)
    join inventory_source as s on (ms.source_id = s.id)
    group by a.bundle_name, a.bundle_version_str, s.id, s.module
    """
    cursor = connection.cursor()
    cursor.execute(query)
    columns = [col[0] for col in cursor.description]
    for row in cursor.fetchall():
        d = dict(zip(columns, row))
        d['source'] = '{}#{}'.format(d.pop('source_module'), d.pop('source_id'))
        for k, v in d.items():
            if k != 'count' and not v:
                d[k] = '_'
        yield d


def os_version_count():
    query = """
    select o.name, o.major, o.minor, o.patch, o.build, s.id as source_id, s.module as source_module,
    sum((ms.mt_next_id is null and ms.id is not null)::integer) as count
    from inventory_osversion as o
    join inventory_machinesnapshot as ms on (ms.os_version_id = o.id)
    join inventory_source as s on (ms.source_id = s.id)
    group by o.name, o.major, o.minor, o.patch, o.build, s.id, s.module
    """
    cursor = connection.cursor()
    cursor.execute(query)
    columns = [col[0] for col in cursor.description]
    for row in cursor.fetchall():
        d = dict(zip(columns, row))
        d['source'] = '{}#{}'.format(d.pop('source_module'), d.pop('source_id'))
        for k, v in d.items():
            if k != 'count' and not v:
                d[k] = '_'
        yield d


def push_inventory_metrics():
    ppg = settings.get('apps', {}).get('zentral.contrib.inventory', {}).get('prometheus_push_gateway', None)
    if not ppg:
        return
    registry = CollectorRegistry()
    g = Gauge('zentral_inventory_osx_apps', 'Zentral inventory OSX apps',
              ['name', 'version_str', 'source'],
              registry=registry)
    for r in osx_app_count():
        count = r.pop('count')
        g.labels(r).set(count)
    g = Gauge('zentral_inventory_os_versions', 'Zentral inventory OS Versions',
              ['name', 'major', 'minor', 'patch', 'build', 'source'],
              registry=registry)
    for r in os_version_count():
        count = r.pop('count')
        g.labels(r).set(count)
    push_to_gateway(ppg, job='zentral_push_inventory_metrics', registry=registry)
