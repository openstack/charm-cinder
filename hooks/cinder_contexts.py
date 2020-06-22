# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from charmhelpers.core.hookenv import (
    config,
    relation_ids,
    service_name,
    related_units,
    relation_get,
    log,
    WARNING,
)

from charmhelpers.contrib.openstack.context import (
    OSContextGenerator,
    ApacheSSLContext as SSLContext,
    SubordinateConfigContext,
)

from charmhelpers.contrib.openstack.utils import (
    os_release,
    CompareOpenStackReleases,
)

from charmhelpers.contrib.hahelpers.cluster import (
    determine_apache_port,
    determine_api_port,
)

CHARM_CEPH_CONF = '/var/lib/charm/{}/ceph.conf'


def enable_lvm():
    """Check whether the LVM backend should be configured

    @returns boolean - If LVM should be enabled"""
    block_device = config('block-device') or 'none'
    return block_device.lower() != 'none'


def ceph_config_file():
    return CHARM_CEPH_CONF.format(service_name())


class ImageServiceContext(OSContextGenerator):
    interfaces = ['image-service']

    def __call__(self):
        if not relation_ids('image-service'):
            return {}
        return {'glance_api_version': config('glance-api-version')}


class CephContext(OSContextGenerator):
    interfaces = ['ceph-cinder']

    def __call__(self):
        """Used to generate template context to be added to cinder.conf in the
        presence of a ceph relation.
        """
        # TODO(this should call is_relation_made)
        if not relation_ids('ceph'):
            return {}
        service = service_name()
        cmp_os_release = CompareOpenStackReleases(os_release('cinder-common'))
        if cmp_os_release >= "icehouse":
            volume_driver = 'cinder.volume.drivers.rbd.RBDDriver'
        else:
            volume_driver = 'cinder.volume.driver.RBDDriver'
        if cmp_os_release >= "ocata":
            driver_key = 'ceph_volume_driver'
        else:
            driver_key = 'volume_driver'
        return {
            driver_key: volume_driver,
            # ensure_ceph_pool() creates pool based on service name.
            'rbd_pool': service,
            'rbd_user': service,
            'host': service,
            'rbd_ceph_conf': ceph_config_file()
        }


class HAProxyContext(OSContextGenerator):
    interfaces = ['cinder-haproxy']

    def __call__(self):
        '''Extends the main charmhelpers HAProxyContext with a port mapping
        specific to this charm.
        Also used to extend cinder.conf context with correct api_listening_port
        '''
        haproxy_port = config('api-listening-port')
        api_port = determine_api_port(config('api-listening-port'),
                                      singlenode_mode=True)
        apache_port = determine_apache_port(config('api-listening-port'),
                                            singlenode_mode=True)

        ctxt = {
            'service_ports': {'cinder_api': [haproxy_port, apache_port]},
            'osapi_volume_listen_port': api_port,
            'port': api_port,
        }
        return ctxt


class ApacheSSLContext(SSLContext):
    interfaces = ['https']
    external_ports = [8776]
    service_namespace = 'cinder'

    def __call__(self):
        # late import to work around circular dependency
        from cinder_utils import service_enabled
        if not service_enabled('api'):
            return {}
        return super(ApacheSSLContext, self).__call__()


class StorageBackendContext(OSContextGenerator):
    interfaces = ['storage-backend']

    def __call__(self):
        backends = []
        for rid in relation_ids('storage-backend'):
            for unit in related_units(rid):
                backend_name = relation_get('backend_name',
                                            unit, rid)
                if backend_name:
                    backends.append(backend_name)
        # Ocata onwards all backends must be in there own sectional config
        if CompareOpenStackReleases(os_release('cinder-common')) >= "ocata":
            if relation_ids('ceph'):
                backends.append('CEPH')
            if enable_lvm():
                backends.append('LVM')
            # Use the package default backend to stop the service flapping.
            if not backends:
                backends = ['LVM']
        return {
            'active_backends': backends,
            'backends': ",".join(backends),
            'default_volume_type': config('default-volume-type')}


class LoggingConfigContext(OSContextGenerator):

    def __call__(self):
        return {'debug': config('debug'), 'verbose': config('verbose')}


class CinderSubordinateConfigContext(SubordinateConfigContext):

    def __call__(self):
        ctxt = super(CinderSubordinateConfigContext, self).__call__()

        # If all backends are stateless we can allow host setting to be set
        # across hosts/units to allow for HA volume failover but otherwise we
        # have to leave it as unique (LP: #1493931).
        rids = []
        for interface in self.interfaces:
            rids.extend(relation_ids(interface))

        stateless = None
        any_stateless = False
        for rid in rids:
            for unit in related_units(rid):
                val = relation_get('stateless', rid=rid, unit=unit) or ""
                if val.lower() == 'true':
                    if stateless is None:
                        stateless = True
                    else:
                        stateless = stateless and True
                else:
                    stateless = False

                any_stateless = any_stateless or stateless

        if stateless:
            if 'DEFAULT' in ctxt['sections']:
                ctxt['sections']['DEFAULT'].append(('host', service_name()))
            else:
                ctxt['sections']['DEFAULT'] = [('host', service_name())]

        elif any_stateless:
            log("One or more stateless backends configured but unable to "
                "set host param since there appear to also be stateful "
                "backends configured.", level=WARNING)

        return ctxt


class RegionContext(OSContextGenerator):
    """Provides context data regarding the region the service is in.

    This context provides the region that is configured by the admin via the
    region option in the config settings for the charm. If no region config
    is available, then this will provide an empty context.
    """
    def __call__(self):
        region = config('region')
        if region:
            return {'region': region}
        else:
            return {}


class SectionalConfigContext(OSContextGenerator):
    """Using DEFAULT config section to configure backends cannot be used
       with Ocata+. In this case each backend needs its own section

    @returns dict - Context dictating if sectional config needs to be used
    """

    def __call__(self):
        cmp_os_release = CompareOpenStackReleases(os_release('cinder-common'))
        return {
            'sectional_default_config': cmp_os_release >= "ocata"
        }


class LVMContext(OSContextGenerator):
    """Context describing the configuration of the LVM backend

    @returns dict - Context describing LVM config
    """
    def __call__(self):
        ctxt = {}
        if enable_lvm():
            ctxt = {
                'volumes_dir': '/var/lib/cinder/volumes',
                'volume_name_template': 'volume-%s',
                'volume_group': config('volume-group'),
                'volume_driver': 'cinder.volume.drivers.lvm.LVMVolumeDriver',
                'volume_backend_name': 'LVM'}
        return ctxt


class VolumeUsageAuditContext(OSContextGenerator):
    """This context provides the configuration directive
    volume_usage_audit_period and also creates a crontab entry
    for running the cinder-volume-usage-audit script recurrently.
    """
    DEFAULT_CRONTAB_PATH = '/etc/cron.d/cinder-volume-usage-audit'

    def __call__(self):
        log("Installing crontab: {}".format(self.DEFAULT_CRONTAB_PATH))
        with open(self.DEFAULT_CRONTAB_PATH, "wt+") as crontab:
            # The cinder-volume-usage-audit executable will only gather
            # data that fits on the configured volume-usage-audit-period.
            crontab.write('0 * * * * root '
                          '/usr/bin/cinder-volume-usage-audit\n')
        return {
            'volume_usage_audit_period': config("volume-usage-audit-period")
        }
