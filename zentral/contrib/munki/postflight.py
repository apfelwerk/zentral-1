#!/usr/bin/python
import json
import plistlib
import ssl
import subprocess
import urllib2

APPLICATION_INVENTORY = "/Library/Managed Installs/ApplicationInventory.plist"
SYSTEM_PROFILER = "/usr/sbin/system_profiler"

ZENTRAL_INVENTORY_API_ENDPOINT = "THE API URL"
ZENTRAL_INVENTORY_API_HEADER = "Zentral-Inventory-API-Key"
ZENTRAL_INVENTORY_API_KEY = "THE API KEY"
ZENTRAL_INVENTORY_API_SERVER_CERTIFICATE = "AN EVENTUAL SELF SIGNED CERTIFICATE PATH"



class ApplicationInventory(object):
    def __init__(self):
        self.data = plistlib.readPlist(APPLICATION_INVENTORY)

    def get_installed_apps(self):
        apps = []
        for app_d in self.data:
            apps.append({'bundle_name': app_d['CFBundleName'],
                         'bundle_id': app_d['bundleid'],
                         'name': app_d['name'],
                         'path': app_d['path'],
                         'version': app_d['version']})
        return apps


class SystemProfilerReport(object):
    def __init__(self):
        p = subprocess.Popen([SYSTEM_PROFILER, '-xml', 'SPHardwareDataType', 'SPSoftwareDataType', 'SPStorageDataType'],
                             stdout=subprocess.PIPE)
        stdoutdata, _ = p.communicate()
        self.data = plistlib.readPlistFromString(stdoutdata)

    def _get_data_type(self, data_type):
        for subdata in self.data:
            if subdata['_dataType'] == data_type:
                return subdata

    def _get_hd(self):
        data = self._get_data_type('SPStorageDataType')
        hd_number = hd_space = hd_total = hd_encrypted = hd_usage = encryption_status = 0
        for item_d in data['_items']:
            if item_d.get('physical_drive', {}).get('is_internal_disk', None) == 'no':
                # skip dmg, ... TODO: TEST !
                continue
            hd_number += 1
            hd_space += item_d['size_in_bytes']
            hd_total += item_d['size_in_bytes'] - item_d['free_space_in_bytes']
            lv_info_d = item_d.get('com.apple.corestorage.lv', None)
            if lv_info_d:
                if lv_info_d.get('com.apple.corestorage.lv.encrypted', None) == 'yes' and\
                   lv_info_d.get('com.apple.corestorage.lv.conversionState', None) == 'Complete':
                    # approximation. could use 'com.apple.corestorage.lv.bytesConverted'
                    # but it seems it is always less than size_in_bytes and could lead to 99%
                    # encryption status.
                    # TODO: better ! 
                    hd_encrypted += item_d['size_in_bytes']
        if hd_space:
            hd_usage = hd_total * 100 / hd_space
            encryption_status = hd_encrypted * 100 / hd_space
        return {'hd_number': hd_number,
                'hd_space': hd_space,
                'hd_total': hd_total,
                'hd_encrypted': hd_encrypted,
                'hd_usage': hd_usage,
                'encryption_status': encryption_status}

    def _get_hw(self):
        data = self._get_data_type('SPHardwareDataType')
        if len(data['_items']) != 1:
            raise ValueError('0 or more than one item in a SPHardwareDataType output!')
        item_d = data['_items'][0]
        hw_d = {'make': 'Apple',  # TODO: always ???
                'model': item_d['machine_model'],
                'name': item_d['machine_name'],
                'serial_number': item_d['serial_number'],
                'processor_type': item_d['cpu_type']}
        # RAM
        ram_amount, ram_amount_unit = item_d['physical_memory'].split()
        if ram_amount_unit == 'GB':
            ram_multiplicator = 2**30
        elif ram_amount_unit == 'MB':
            ram_multiplicator = 2**20
        else:
            raise ValueError('Unknown ram amount unit %s' % ram_amount_unit)
        hw_d['ram_total'] = int(ram_amount) * ram_multiplicator
        # CPU SPEED
        cpu_speed, cpu_speed_unit = item_d['current_processor_speed'].split()
        if cpu_speed_unit == 'GHz':
            cpu_speed_multiplicator = 10**3
        elif cpu_speed_unit == 'MHz':
            cpu_speed_multiplicator = 10**0
        else:
            raise ValueError('Unknown cpu speed unit %s' % cpu_speed_unit)
        hw_d['processor_speed'] = int(float(cpu_speed.replace(',', '.')) * cpu_speed_multiplicator)
        return hw_d

    def _get_soft(self):
        data = self._get_data_type('SPSoftwareDataType')
        if len(data['_items']) != 1:
            raise ValueError('0 or more than one item in a SPSoftwareDataType output!')
        item_d = data['_items'][0]
        os_version = item_d['os_version']
        os_name, os_version, os_build = os_version.rsplit(' ', 2)
        os_build = os_build.strip('(').strip(')')
        return {'os_name': os_name,
                'os_version': os_version,
                'os_build': os_build}

    def get_machine_d(self):
        machine_d = self._get_hd()
        machine_d.update(self._get_hw())
        machine_d.update(self._get_soft())
        return machine_d


def post_machine_d(machine_d):
    req = urllib2.Request(ZENTRAL_INVENTORY_API_ENDPOINT)
    req.add_header('Content-Type', 'application/json')
    req.add_header(ZENTRAL_INVENTORY_API_HEADER, ZENTRAL_INVENTORY_API_KEY)
    if ZENTRAL_INVENTORY_API_SERVER_CERTIFICATE:
        ctx = ssl.create_default_context(cafile=ZENTRAL_INVENTORY_API_SERVER_CERTIFICATE)
    else:
        ctx = ssl.create_default_context()
    response = urllib2.urlopen(req, json.dumps(machine_d), context=ctx)
    return json.load(response)


if __name__ == '__main__':
    spr = SystemProfilerReport()
    ai = ApplicationInventory()
    machine_d = spr.get_machine_d()
    machine_d['osx_apps'] = ai.get_installed_apps()
    diff = post_machine_d(machine_d)
    if diff:
        import pprint
        pprint.pprint(diff)
