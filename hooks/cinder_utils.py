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

from __future__ import print_function

import os
import re
import subprocess
import uuid

from copy import deepcopy
from collections import OrderedDict
from copy import copy
from tempfile import NamedTemporaryFile

from charmhelpers.core.strutils import (
    bytes_from_string
)

from charmhelpers.core.hookenv import (
    config,
    local_unit,
    relation_get,
    relation_set,
    relation_ids,
    related_units,
    log,
    DEBUG,
)

from charmhelpers.fetch import (
    apt_upgrade,
    apt_update,
    apt_install,
    add_source,
    apt_purge,
    apt_autoremove,
    filter_missing_packages,
)

from charmhelpers.core.host import (
    CompareHostReleases,
    lsb_release,
    mkdir,
    mounts,
    umount,
    service_restart,
    service_stop,
    service_start,
)

from charmhelpers.contrib.openstack.alternatives import install_alternative
from charmhelpers.contrib.hahelpers.cluster import (
    is_elected_leader,
    get_hacluster_config,
)

from charmhelpers.contrib.storage.linux.utils import (
    is_block_device,
    zap_disk,
    is_device_mounted,
)

from charmhelpers.contrib.storage.linux.lvm import (
    create_lvm_physical_volume,
    create_lvm_volume_group,
    deactivate_lvm_volume_group,
    extend_logical_volume_by_device,
    is_lvm_physical_volume,
    list_lvm_volume_group,
    list_thin_logical_volume_pools,
    remove_lvm_physical_volume,
)

from charmhelpers.contrib.storage.linux.loopback import (
    ensure_loopback_device,
)

from charmhelpers.contrib.openstack import (
    templating,
    context,
)

from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    get_os_codename_install_source,
    os_release,
    reset_os_release,
    make_assess_status_func,
    pause_unit,
    resume_unit,
    is_unit_paused_set,
    os_application_version_set,
    token_cache_pkgs,
    enable_memcache,
    CompareOpenStackReleases,
)

from charmhelpers.core.decorators import (
    retry_on_exception,
)

import cinder_contexts

from cinder_contexts import ceph_config_file

COMMON_PACKAGES = [
    'apache2',
    'cinder-common',
    'gdisk',
    'haproxy',
    'librbd1',  # bug 1440948 vol-from-img
    'python-jinja2',
    'python-keystoneclient',
    'python-mysqldb',
    'python-psycopg2',
    'qemu-utils',
    'thin-provisioning-tools',
]

PY3_PACKAGES = [
    'python3-cinder',
    'python3-memcache',
    'python3-rados',
    'python3-rbd',
    'libapache2-mod-wsgi-py3',
]

API_PACKAGES = ['cinder-api']
VOLUME_PACKAGES = ['cinder-volume']
SCHEDULER_PACKAGES = ['cinder-scheduler']

DEFAULT_LOOPBACK_SIZE = '5G'

# Cluster resource used to determine leadership when hacluster'd
CLUSTER_RES = 'grp_cinder_vips'

CINDER_DB_INIT_RKEY = 'cinder-db-initialised'
CINDER_DB_INIT_ECHO_RKEY = 'cinder-db-initialised-echo'


class CinderCharmError(Exception):
    pass

CINDER_CONF_DIR = "/etc/cinder"
CINDER_CONF = '%s/cinder.conf' % CINDER_CONF_DIR
CINDER_API_CONF = '%s/api-paste.ini' % CINDER_CONF_DIR
CEPH_CONF = '/etc/ceph/ceph.conf'

HAPROXY_CONF = '/etc/haproxy/haproxy.cfg'
APACHE_SITE_CONF = '/etc/apache2/sites-available/openstack_https_frontend'
APACHE_SITE_24_CONF = '/etc/apache2/sites-available/' \
    'openstack_https_frontend.conf'
MEMCACHED_CONF = '/etc/memcached.conf'
WSGI_CINDER_API_CONF = '/etc/apache2/sites-enabled/wsgi-openstack-api.conf'
PACKAGE_CINDER_API_CONF = '/etc/apache2/conf-enabled/cinder-wsgi.conf'

VERSION_PACKAGE = 'cinder-common'

TEMPLATES = 'templates/'

# The interface is said to be satisfied if anyone of the interfaces in
# the
# list has a complete context.
REQUIRED_INTERFACES = {
    'database': ('shared-db',),
    'messaging': ('amqp',),
    'identity': ('identity-service',),
}


def required_interfaces():
    """Provide the required charm interfaces based on configured roles."""
    _interfaces = copy(REQUIRED_INTERFACES)
    if not service_enabled('api'):
        # drop requirement for identity interface
        _interfaces.pop('identity')

    return _interfaces


# Map config files to hook contexts and services that will be associated
# with file in restart_on_changes()'s service map.
BASE_RESOURCE_MAP = OrderedDict([
    (CINDER_CONF, {
        'contexts': [context.SharedDBContext(ssl_dir=CINDER_CONF_DIR),
                     context.AMQPContext(ssl_dir=CINDER_CONF_DIR),
                     context.ImageServiceContext(),
                     context.OSConfigFlagContext(),
                     context.SyslogContext(),
                     cinder_contexts.CephContext(),
                     cinder_contexts.HAProxyContext(),
                     cinder_contexts.ImageServiceContext(),
                     cinder_contexts.CinderSubordinateConfigContext(
                         interface=['storage-backend', 'backup-backend'],
                         service='cinder',
                         config_file=CINDER_CONF),
                     cinder_contexts.StorageBackendContext(),
                     cinder_contexts.LoggingConfigContext(),
                     context.IdentityServiceContext(
                         service='cinder',
                         service_user='cinder'),
                     context.BindHostContext(),
                     context.WorkerConfigContext(),
                     cinder_contexts.RegionContext(),
                     context.InternalEndpointContext(),
                     cinder_contexts.VolumeUsageAuditContext(),
                     context.MemcacheContext(),
                     cinder_contexts.SectionalConfigContext(),
                     cinder_contexts.LVMContext()],
        'services': ['cinder-api', 'cinder-volume', 'cinder-scheduler',
                     'haproxy']
    }),
    (CINDER_API_CONF, {
        'contexts': [context.IdentityServiceContext()],
        'services': ['cinder-api'],
    }),
    (ceph_config_file(), {
        'contexts': [context.CephContext()],
        'services': ['cinder-volume']
    }),
    (HAPROXY_CONF, {
        'contexts': [context.HAProxyContext(singlenode_mode=True),
                     cinder_contexts.HAProxyContext()],
        'services': ['haproxy'],
    }),
    (APACHE_SITE_CONF, {
        'contexts': [cinder_contexts.ApacheSSLContext()],
        'services': ['apache2'],
    }),
    (APACHE_SITE_24_CONF, {
        'contexts': [cinder_contexts.ApacheSSLContext()],
        'services': ['apache2'],
    }),
])


def register_configs(release=None):
    """Register config files with their respective contexts.
    Regstration of some configs may not be required depending on
    existing of certain relations.
    """
    # if called without anything installed (eg during install hook)
    # just default to earliest supported release. configs dont get touched
    # till post-install, anyway.
    release = release or os_release('cinder-common', base='icehouse')
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)
    for cfg, rscs in resource_map().iteritems():
        configs.register(cfg, rscs['contexts'])
    return configs


def resource_map(release=None):
    """
    Dynamically generate a map of resources that will be managed for a single
    hook execution.
    """
    resource_map = deepcopy(BASE_RESOURCE_MAP)
    if relation_ids('backup-backend'):
        resource_map[CINDER_CONF]['services'].append('cinder-backup')
        resource_map[ceph_config_file()]['services'].append('cinder-backup')

    if relation_ids('ceph'):
        # need to create this early, new peers will have a relation during
        # registration # before they've run the ceph hooks to create the
        # directory.
        # !!! FIX: These side effects seem inappropriate for this method
        mkdir(os.path.dirname(CEPH_CONF))
        mkdir(os.path.dirname(ceph_config_file()))

        # Install ceph config as an alternative for co-location with
        # ceph and ceph-osd charm - cinder ceph.conf will be
        # lower priority than both of these but thats OK
        if not os.path.exists(ceph_config_file()):
            # touch file for pre-templated generation
            open(ceph_config_file(), 'w').close()
        install_alternative(os.path.basename(CEPH_CONF),
                            CEPH_CONF, ceph_config_file())
    else:
        resource_map.pop(ceph_config_file())

    if os.path.exists('/etc/apache2/conf-available'):
        resource_map.pop(APACHE_SITE_CONF)
    else:
        resource_map.pop(APACHE_SITE_24_CONF)

    # Remove services from map which are not enabled by user config
    for cfg in resource_map.keys():
        resource_map[cfg]['services'] = \
            filter_services(resource_map[cfg]['services'])

    if enable_memcache(source=config()['openstack-origin']):
        resource_map[MEMCACHED_CONF] = {
            'contexts': [context.MemcacheContext()],
            'services': ['memcached']}

    if run_in_apache():
        for cfile in resource_map:
            svcs = resource_map[cfile]['services']
            if 'cinder-api' in svcs:
                svcs.remove('cinder-api')
                if 'apache2' not in svcs:
                    svcs.append('apache2')
        wsgi_script = "/usr/bin/cinder-wsgi"
        resource_map[WSGI_CINDER_API_CONF] = {
            'contexts': [context.WSGIWorkerConfigContext(name="cinder",
                                                         script=wsgi_script),
                         cinder_contexts.HAProxyContext()],
            'services': ['apache2']
        }

    return resource_map


def filter_services(svcs):
    '''Remove services not enabled by user config from a list of services

    @param svcs: List of services
    @returns : List of enabled services
    '''
    return [s for s in svcs
            if service_enabled(s.lstrip('cinder-')) or
            not s.startswith('cinder')]


def juju_log(msg):
    log('[cinder] %s' % msg)


def determine_packages():
    '''Determine list of packages required for the currently enabled services.

    :returns: list of package names
    '''
    pkgs = copy(COMMON_PACKAGES)
    for s, p in [('api', API_PACKAGES),
                 ('volume', VOLUME_PACKAGES),
                 ('scheduler', SCHEDULER_PACKAGES)]:
        if service_enabled(s):
            pkgs += p

    pkgs.extend(token_cache_pkgs(source=config()['openstack-origin']))

    if CompareOpenStackReleases(os_release('cinder')) >= 'rocky':
        pkgs = [p for p in pkgs if not p.startswith('python-')]
        pkgs.extend(PY3_PACKAGES)

    return pkgs


def determine_purge_packages():
    '''
    Determine list of packages that where previously installed which are no
    longer needed.

    :returns: list of package names
    '''
    if CompareOpenStackReleases(os_release('cinder')) >= 'rocky':
        pkgs = [p for p in COMMON_PACKAGES if p.startswith('python-')]
        pkgs.append('python-cinder')
        pkgs.append('python-memcache')
        return pkgs
    return []


def remove_old_packages():
    '''Purge any packages that need ot be removed.

    :returns: bool Whether packages were removed.
    '''
    installed_packages = filter_missing_packages(determine_purge_packages())
    if installed_packages:
        apt_purge(installed_packages, fatal=True)
        apt_autoremove(purge=True, fatal=True)
    return bool(installed_packages)


def service_enabled(service):
    '''Determine if a specific cinder service is enabled in
    charm configuration.

    :param service: str: cinder service name to query (volume, scheduler, api,
                         all)

    :returns: boolean: True if service is enabled in config, False if not.
    '''
    enabled = config()['enabled-services']
    if enabled == 'all':
        return True
    return service in enabled


def restart_map():
    '''Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :returns: dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    '''
    return OrderedDict([(cfg, v['services'])
                        for cfg, v in resource_map().iteritems()
                        if v['services']])


def enabled_services():
    m = restart_map()
    svcs = set()
    for t in m.iteritems():
        svcs.update(t[1])

    return list(svcs)


def services():
    ''' Returns a list of services associate with this charm '''
    _services = []
    for v in restart_map().values():
        _services = _services + v
    return list(set(_services))


def reduce_lvm_volume_group_missing(volume_group, extra_args=None):
    '''
    Remove all missing physical volumes from the volume group, if there
    are no logical volumes allocated on them.

    :param volume_group: str: Name of volume group to reduce.
    :param extra_args: list: List of extra args to pass to vgreduce
    '''
    if extra_args is None:
        extra_args = []

    command = ['vgreduce', '--removemissing'] + extra_args + [volume_group]
    subprocess.check_call(command)


def extend_lvm_volume_group(volume_group, block_device):
    '''
    Extend an LVM volume group onto a given block device.

    Assumes block device has already been initialized as an LVM PV.

    :param volume_group: str: Name of volume group to create.
    :block_device: str: Full path of PV-initialized block device.
    '''
    subprocess.check_call(['vgextend', volume_group, block_device])


def lvm_volume_group_exists(volume_group):
    """Check for the existence of a volume group.

    :param volume_group: str: Name of volume group.
    """
    try:
        subprocess.check_call(['vgdisplay', volume_group])
    except subprocess.CalledProcessError:
        return False
    else:
        return True


def remove_lvm_volume_group(volume_group):
    """Remove a volume group.

    :param volume_group: str: Name of volume group to remove.
    """
    subprocess.check_call(['vgremove', '--force', volume_group])


def ensure_lvm_volume_group_non_existent(volume_group):
    """Remove volume_group if it exists.

    :param volume_group: str: Name of volume group.
    """
    if not lvm_volume_group_exists(volume_group):
        return

    remove_lvm_volume_group(volume_group)


def log_lvm_info():
    """Log some useful information about how LVM is setup."""
    pvscan_output = subprocess.check_output(['pvscan'])
    juju_log('pvscan: %s' % pvscan_output)


def configure_lvm_storage(block_devices, volume_group, overwrite=False,
                          remove_missing=False, remove_missing_force=False):
    ''' Configure LVM storage on the list of block devices provided

    :param block_devices: list: List of whitelisted block devices to detect
                                and use if found
    :param overwrite: bool: Scrub any existing block data if block device is
                            not already in-use
    :param remove_missing: bool: Remove missing physical volumes from volume
                           group if logical volume not allocated on them
    :param remove_missing_force: bool: Remove missing physical volumes from
                           volume group even if logical volumes are allocated
                           on them. Overrides 'remove_missing' if set.
    '''
    log_lvm_info()
    devices = []
    for block_device in block_devices:
        (block_device, size) = _parse_block_device(block_device)

        if not is_device_mounted(block_device):
            if size == 0 and is_block_device(block_device):
                devices.append(block_device)
            elif size > 0:
                devices.append(ensure_loopback_device(block_device, str(size)))

    # NOTE(jamespage)
    # might need todo an initial one-time scrub on install if need be
    vg_found = False
    new_devices = []
    for device in devices:
        if not is_lvm_physical_volume(device):
            # Unused device
            if overwrite is True or not has_partition_table(device):
                prepare_volume(device)
                new_devices.append(device)
        elif (is_lvm_physical_volume(device) and
              list_lvm_volume_group(device) != volume_group):
            # Existing LVM but not part of required VG or new device
            if overwrite is True:
                prepare_volume(device)
                new_devices.append(device)
        elif (is_lvm_physical_volume(device) and
                list_lvm_volume_group(device) == volume_group):
            # Mark vg as found
            vg_found = True

    log_lvm_info()

    if vg_found is False and len(new_devices) > 0:
        if overwrite:
            ensure_lvm_volume_group_non_existent(volume_group)

        # Create new volume group from first device
        create_lvm_volume_group(volume_group, new_devices[0])
        new_devices.remove(new_devices[0])

    # Remove missing physical volumes from volume group
    if remove_missing_force:
        reduce_lvm_volume_group_missing(volume_group, extra_args=['--force'])
    elif remove_missing:
        reduce_lvm_volume_group_missing(volume_group)

    if len(new_devices) > 0:
        # Extend the volume group as required
        for new_device in new_devices:
            extend_lvm_volume_group(volume_group, new_device)
            thin_pools = list_thin_logical_volume_pools(path_mode=True)
            if len(thin_pools) == 0:
                juju_log("No thin pools found")
            elif len(thin_pools) == 1:
                juju_log("Thin pool {} found, extending with {}".format(
                    thin_pools[0],
                    new_device))
                extend_logical_volume_by_device(thin_pools[0], new_device)
            else:
                juju_log("Multiple thin pools ({}) found, "
                         "skipping auto extending with {}".format(
                             ','.join(thin_pools),
                             new_device))
    log_lvm_info()


def prepare_volume(device):
    clean_storage(device)
    create_lvm_physical_volume(device)


def has_partition_table(block_device):
    out = subprocess.check_output(['fdisk', '-l', block_device],
                                  stderr=subprocess.STDOUT)
    return "doesn't contain a valid partition" not in out


def clean_storage(block_device):
    '''Ensures a block device is clean.  That is:
        - unmounted
        - any lvm volume groups are deactivated
        - any lvm physical device signatures removed
        - partition table wiped

    :param block_device: str: Full path to block device to clean.
    '''
    for mp, d in mounts():
        if d == block_device:
            juju_log('clean_storage(): Found %s mounted @ %s, unmounting.' %
                     (d, mp))
            umount(mp, persist=True)

    if is_lvm_physical_volume(block_device):
        deactivate_lvm_volume_group(block_device)
        remove_lvm_physical_volume(block_device)

    zap_disk(block_device)


def _parse_block_device(block_device):
    ''' Parse a block device string and return either the full path
    to the block device, or the path to a loopback device and its size

    :param: block_device: str: Block device as provided in configuration

    :returns: (str, int): Full path to block device and 0 OR
                          Full path to loopback device and required size
    '''
    _none = ['None', 'none', None]
    if block_device in _none:
        return (None, 0)
    if block_device.startswith('/dev/'):
        return (block_device, 0)
    elif block_device.startswith('/'):
        _bd = block_device.split('|')
        if len(_bd) == 2:
            bdev, size = _bd
        else:
            bdev = block_device
            size = DEFAULT_LOOPBACK_SIZE
        return (bdev, bytes_from_string(str(size)))
    else:
        return ('/dev/{}'.format(block_device), 0)


def is_db_initialised(cluster_rid=None):
    """
    Check whether a db intialisation has been performed by any peer unit.

    We base our decision on whether we or any of our peers has previously
    sent or echoed an initialisation notification.

    @param cluster_rid: current relation id. If none provided, all cluster
                        relation ids will be checked.
    @return: True if there has been a db initialisation otherwise False.
    """
    if cluster_rid:
        rids = [cluster_rid]
    else:
        rids = relation_ids('cluster')

    for c_rid in rids:
        units = related_units(relid=c_rid) + [local_unit()]
        for unit in units:
            settings = relation_get(unit=unit, rid=c_rid) or {}
            for key in [CINDER_DB_INIT_RKEY, CINDER_DB_INIT_ECHO_RKEY]:
                if settings.get(key):
                    return True

    return False


def is_new_dbinit_notification(init_id, echoed_init_id):
    """Returns True if we have a received a new db initialisation notification
    from a peer unit and we have not previously echoed it to indicate that we
    have already performed the necessary actions as result.

    @param init_db: received initialisation notification.
    @param echoed_init_db: value currently set for the echo key.
    @return: True if new notification and False if not.
    """
    return (init_id and (local_unit() not in init_id) and
            (echoed_init_id != init_id))


def check_local_db_actions_complete():
    """Check if we have received db init'd notification and restart services
    if we have not already.

    NOTE: this must only be called from peer relation context.
    """
    if not is_db_initialised():
        return

    settings = relation_get() or {}
    if settings:
        init_id = settings.get(CINDER_DB_INIT_RKEY)
        echoed_init_id = relation_get(unit=local_unit(),
                                      attribute=CINDER_DB_INIT_ECHO_RKEY)

        # If we have received an init notification from a peer unit
        # (assumed to be the leader) then restart cinder-* and echo the
        # notification and don't restart again unless we receive a new
        # (different) notification.
        if is_new_dbinit_notification(init_id, echoed_init_id):
            if not is_unit_paused_set():
                log("Restarting cinder services following db "
                    "initialisation", level=DEBUG)
                for svc in enabled_services():
                    service_restart(svc)

            # Echo notification
            relation_set(**{CINDER_DB_INIT_ECHO_RKEY: init_id})


# NOTE(jamespage): Retry deals with sync issues during one-shot HA deploys.
#                  mysql might be restarting or suchlike.
@retry_on_exception(5, base_delay=3, exc_type=subprocess.CalledProcessError)
def migrate_database(upgrade=False):
    """Initialise cinder database if not already done so.

    Runs cinder-manage to initialize a new database or migrate existing and
    restarts services to ensure that the changes are picked up. The first
    (leader) unit to perform this action should have broadcast this information
    to its peers so first we check whether this has already occurred.
    """
    if not upgrade and is_db_initialised():
        log("Database is already initialised.", level=DEBUG)
        return

    cmd = ['cinder-manage', 'db', 'sync']
    subprocess.check_call(cmd)
    # Notify peers so that services get restarted
    log("Notifying peer(s) that db is initialised and restarting services",
        level=DEBUG)
    for r_id in relation_ids('cluster'):
        if not is_unit_paused_set():
            for svc in enabled_services():
                service_restart(svc)

        id = "%s-%s" % (local_unit(), uuid.uuid4())
        relation_set(relation_id=r_id, **{CINDER_DB_INIT_RKEY: id})


def do_openstack_upgrade(configs=None):
    """Perform an uprade of cinder. Takes care of upgrading
    packages, rewriting configs + database migration and
    potentially any other post-upgrade actions.

    :param configs: The charms main OSConfigRenderer object.

    """
    new_src = config('openstack-origin')
    new_os_rel = get_os_codename_install_source(new_src)

    juju_log('Performing OpenStack upgrade to %s.' % (new_os_rel))

    configure_installation_source(new_src)
    dpkg_opts = [
        '--option', 'Dpkg::Options::=--force-confnew',
        '--option', 'Dpkg::Options::=--force-confdef',
    ]
    apt_update()
    apt_upgrade(options=dpkg_opts, fatal=True, dist=True)
    reset_os_release()
    apt_install(determine_packages(), fatal=True)

    remove_old_packages()

    # NOTE(hopem): must do this after packages have been upgraded so that
    # we ensure that correct configs are selected for the target release.
    # See LP 1726527.
    configs = register_configs()

    # set CONFIGS to load templates from new release and regenerate config
    configs.set_release(openstack_release=new_os_rel)
    configs.write_all()

    if run_in_apache():
        disable_package_apache_site()

    # Stop/start services and migrate DB if leader
    [service_stop(s) for s in services()]
    if is_elected_leader(CLUSTER_RES):
        migrate_database(upgrade=True)
    if not is_unit_paused_set():
        [service_start(s) for s in services()]


def setup_ipv6():
    ubuntu_rel = lsb_release()['DISTRIB_CODENAME'].lower()
    if CompareHostReleases(ubuntu_rel) < "trusty":
        raise Exception("IPv6 is not supported in the charms for Ubuntu "
                        "versions less than Trusty 14.04")

    # Need haproxy >= 1.5.3 for ipv6 so for Trusty if we are <= Kilo we need to
    # use trusty-backports otherwise we can use the UCA.
    if (ubuntu_rel == 'trusty' and
            CompareOpenStackReleases(os_release('cinder')) < 'liberty'):
        add_source('deb http://archive.ubuntu.com/ubuntu trusty-backports '
                   'main')
        apt_update()
        apt_install('haproxy/trusty-backports', fatal=True)


def filesystem_mounted(fs):
    return subprocess.call(['grep', '-wqs', fs, '/proc/mounts']) == 0


def get_optional_interfaces():
    """Return the optional interfaces that should be checked if the relavent
    relations have appeared.
    :returns: {general_interface: [specific_int1, specific_int2, ...], ...}
    """
    optional_interfaces = {}
    if relation_ids('ha'):
        optional_interfaces['ha'] = ['cluster']

    if relation_ids('storage-backend') or relation_ids('ceph'):
        optional_interfaces['storage-backend'] = ['storage-backend', 'ceph']

    if relation_ids('image-service'):
        optional_interfaces['image'] = ['image-service']

    return optional_interfaces


def check_optional_relations(configs):
    """Check that if we have a relation_id for high availability that we can
    get the hacluster config.  If we can't then we are blocked.  This function
    is called from assess_status/set_os_workload_status as the charm_func and
    needs to return either "unknown", "" if there is no problem or the status,
    message if there is a problem.

    :param configs: an OSConfigRender() instance.
    :return 2-tuple: (string, string) = (status, message)
    """
    if relation_ids('ha'):
        try:
            get_hacluster_config()
        except:
            return ('blocked',
                    'hacluster missing configuration: '
                    'vip, vip_iface, vip_cidr')
    # return 'unknown' as the lowest priority to not clobber an existing
    # status.
    return 'unknown', ''


def assess_status(configs):
    """Assess status of current unit
    Decides what the state of the unit should be based on the current
    configuration.
    SIDE EFFECT: calls set_os_workload_status(...) which sets the workload
    status of the unit.
    Also calls status_set(...) directly if paused state isn't complete.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    assess_status_func(configs)()
    os_application_version_set(VERSION_PACKAGE)


def assess_status_func(configs):
    """Helper function to create the function that will assess_status() for
    the unit.
    Uses charmhelpers.contrib.openstack.utils.make_assess_status_func() to
    create the appropriate status function and then returns it.
    Used directly by assess_status() and also for pausing and resuming
    the unit.

    NOTE: REQUIRED_INTERFACES is augmented with the optional interfaces
    depending on the current config before being passed to the
    make_assess_status_func() function.

    NOTE(ajkavanagh) ports are not checked due to race hazards with services
    that don't behave sychronously w.r.t their service scripts.  e.g.
    apache2.
    @param configs: a templating.OSConfigRenderer() object
    @return f() -> None : a function that assesses the unit's workload status
    """
    interfaces = required_interfaces()
    interfaces.update(get_optional_interfaces())
    return make_assess_status_func(
        configs, interfaces,
        charm_func=check_optional_relations,
        services=services(), ports=None)


def pause_unit_helper(configs):
    """Helper function to pause a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.pause_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(pause_unit, configs)


def resume_unit_helper(configs):
    """Helper function to resume a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.resume_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(resume_unit, configs)


def _pause_resume_helper(f, configs):
    """Helper function that uses the make_assess_status_func(...) from
    charmhelpers.contrib.openstack.utils to create an assess_status(...)
    function that can be used with the pause/resume of the unit
    @param f: the function to be used with the assess_status(...) function
    @returns None - this function is executed for its side-effect
    """
    # TODO(ajkavanagh) - ports= has been left off because of the race hazard
    # that exists due to service_start()
    f(assess_status_func(configs),
      services=services(),
      ports=None)


def run_in_apache():
    """Return true if cinder API is run under apache2 with mod_wsgi in
    this release.
    """
    return CompareOpenStackReleases(os_release('cinder-common')) >= 'ocata'


def disable_package_apache_site():
    """Ensure that the package-provided apache configuration is disabled to
    prevent it from conflicting with the charm-provided version.
    """
    if os.path.exists(PACKAGE_CINDER_API_CONF):
        subprocess.check_call(['a2disconf', 'cinder-wsgi'])


def scrub_old_style_ceph():
    """Purge any legacy ceph configuration from install"""
    # NOTE: purge old override file - no longer needed
    if os.path.exists('/etc/init/cinder-volume.override'):
        os.remove('/etc/init/cinder-volume.override')
    # NOTE: purge any CEPH_ARGS data from /etc/environment
    env_file = '/etc/environment'
    ceph_match = re.compile("^CEPH_ARGS.*").search
    with open(env_file, 'r') as input_file:
        with NamedTemporaryFile(mode='w',
                                delete=False,
                                dir=os.path.dirname(env_file)) as outfile:
            for line in input_file:
                if not ceph_match(line):
                    print(line, end='', file=outfile)
            os.rename(outfile.name, input_file.name)
