import os
import subprocess

from collections import OrderedDict
from copy import copy

from charmhelpers.core.hookenv import (
    config,
    relation_ids,
    log,
)

from charmhelpers.fetch import (
    apt_install,
    apt_update,
)

from charmhelpers.core.host import (
    mounts,
    umount,
)

from charmhelpers.contrib.storage.linux.ceph import (
    create_pool as ceph_create_pool,
    pool_exists as ceph_pool_exists,
)

from charmhelpers.contrib.hahelpers.cluster import (
    eligible_leader,
)

from charmhelpers.contrib.storage.linux.utils import (
    is_block_device,
    zap_disk,
)

from charmhelpers.contrib.storage.linux.lvm import (
    create_lvm_physical_volume,
    create_lvm_volume_group,
    deactivate_lvm_volume_group,
    is_lvm_physical_volume,
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
    get_os_codename_package,
    get_os_codename_install_source,
)

import cinder_contexts

COMMON_PACKAGES = [
    'apache2',
    'cinder-common',
    'gdisk',
    'haproxy',
    'python-jinja2',
    'python-keystoneclient',
    'python-mysqldb',
    'qemu-utils',
]

API_PACKAGES = ['cinder-api']
VOLUME_PACKAGES = ['cinder-volume']
SCHEDULER_PACKAGES = ['cinder-scheduler']

DEFAULT_LOOPBACK_SIZE = '5G'

# Cluster resource used to determine leadership when hacluster'd
CLUSTER_RES = 'res_cinder_vip'


class CinderCharmError(Exception):
    pass

CINDER_CONF = '/etc/cinder/cinder.conf'
CINDER_API_CONF = '/etc/cinder/api-paste.ini'
CEPH_CONF = '/etc/ceph/ceph.conf'
HAPROXY_CONF = '/etc/haproxy/haproxy.cfg'
APACHE_SITE_CONF = '/etc/apache2/sites-available/openstack_https_frontend'
APACHE_SITE_24_CONF = '/etc/apache2/sites-available/' \
    'openstack_https_frontend.conf'

TEMPLATES = 'templates/'
# Map config files to hook contexts and services that will be associated
# with file in restart_on_changes()'s service map.
CONFIG_FILES = OrderedDict([
    (CINDER_CONF, {
        'hook_contexts': [context.SharedDBContext(),
                          context.AMQPContext(),
                          context.ImageServiceContext(),
                          context.OSConfigFlagContext(),
                          context.SyslogContext(),
                          cinder_contexts.CephContext(),
                          cinder_contexts.HAProxyContext(),
                          cinder_contexts.ImageServiceContext()],
        'services': ['cinder-api', 'cinder-volume',
                     'cinder-scheduler', 'haproxy']
    }),
    (CINDER_API_CONF, {
        'hook_contexts': [context.IdentityServiceContext()],
        'services': ['cinder-api'],
    }),
    (CEPH_CONF, {
        'hook_contexts': [context.CephContext()],
        'services': ['cinder-volume']
    }),
    (HAPROXY_CONF, {
        'hook_contexts': [context.HAProxyContext(),
                          cinder_contexts.HAProxyContext()],
        'services': ['haproxy'],
    }),
    (APACHE_SITE_CONF, {
        'hook_contexts': [cinder_contexts.ApacheSSLContext()],
        'services': ['apache2'],
    }),
    (APACHE_SITE_24_CONF, {
        'hook_contexts': [cinder_contexts.ApacheSSLContext()],
        'services': ['apache2'],
    }),
])


def register_configs():
    """
    Register config files with their respective contexts.
    Regstration of some configs may not be required depending on
    existing of certain relations.
    """
    # if called without anything installed (eg during install hook)
    # just default to earliest supported release. configs dont get touched
    # till post-install, anyway.
    release = get_os_codename_package('cinder-common', fatal=False) or 'folsom'
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)

    confs = [CINDER_API_CONF,
             CINDER_CONF,
             HAPROXY_CONF]

    if relation_ids('ceph'):
        # need to create this early, new peers will have a relation during
        # registration # before they've run the ceph hooks to create the
        # directory.
        if not os.path.isdir(os.path.dirname(CEPH_CONF)):
            os.mkdir(os.path.dirname(CEPH_CONF))
        confs.append(CEPH_CONF)

    for conf in confs:
        configs.register(conf, CONFIG_FILES[conf]['hook_contexts'])

    if os.path.exists('/etc/apache2/conf-available'):
        configs.register(APACHE_SITE_24_CONF,
                         CONFIG_FILES[APACHE_SITE_24_CONF]['hook_contexts'])
    else:
        configs.register(APACHE_SITE_CONF,
                         CONFIG_FILES[APACHE_SITE_CONF]['hook_contexts'])
    return configs


def juju_log(msg):
    log('[cinder] %s' % msg)


def determine_packages():
    '''
    Determine list of packages required for the currently enabled services.

    :returns: list of package names
    '''
    pkgs = copy(COMMON_PACKAGES)
    for s, p in [('api', API_PACKAGES),
                 ('volume', VOLUME_PACKAGES),
                 ('scheduler', SCHEDULER_PACKAGES)]:
        if service_enabled(s):
            pkgs += p
    return pkgs


def service_enabled(service):
    '''
    Determine if a specific cinder service is enabled in charm configuration.

    :param service: str: cinder service name to query (volume, scheduler, api,
                         all)

    :returns: boolean: True if service is enabled in config, False if not.
    '''
    enabled = config()['enabled-services']
    if enabled == 'all':
        return True
    return service in enabled


def restart_map():
    '''
    Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :returns: dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    '''
    _map = []
    for f, ctxt in CONFIG_FILES.iteritems():
        svcs = []
        for svc in ctxt['services']:
            if svc.startswith('cinder-'):
                if service_enabled(svc.split('-')[1]):
                    svcs.append(svc)
            else:
                svcs.append(svc)
        if svcs:
            _map.append((f, svcs))
    return OrderedDict(_map)


def prepare_lvm_storage(block_device, volume_group):
    '''
    Ensures block_device is initialized as a LVM PV and creates volume_group.
    Assumes block device is clean and will raise if storage is already
    initialized as a PV.

    :param block_device: str: Full path to block device to be prepared.
    :param volume_group: str: Name of volume group to be created with
                              block_device as backing PV.

    :returns: None or raises CinderCharmError if storage is unclean.
    '''
    e = None
    if is_lvm_physical_volume(block_device):
        juju_log('ERROR: Could not prepare LVM storage: %s is already '
                 'initialized as LVM physical device.' % block_device)
        raise CinderCharmError

    try:
        create_lvm_physical_volume(block_device)
        create_lvm_volume_group(volume_group, block_device)
    except Exception as e:
        juju_log('Could not prepare LVM storage on %s.' % block_device)
        juju_log(e)
        raise CinderCharmError


def clean_storage(block_device):
    '''
    Ensures a block device is clean.  That is:
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
    else:
        zap_disk(block_device)


def ensure_block_device(block_device):
    '''
    Confirm block_device, create as loopback if necessary.

    :param block_device: str: Full path of block device to ensure.

    :returns: str: Full path of ensured block device.
    '''
    _none = ['None', 'none', None]
    if (block_device in _none):
        juju_log('prepare_storage(): Missing required input: '
                 'block_device=%s.' % block_device)
        raise CinderCharmError

    if block_device.startswith('/dev/'):
        bdev = block_device
    elif block_device.startswith('/'):
        _bd = block_device.split('|')
        if len(_bd) == 2:
            bdev, size = _bd
        else:
            bdev = block_device
            size = DEFAULT_LOOPBACK_SIZE
        bdev = ensure_loopback_device(bdev, size)
    else:
        bdev = '/dev/%s' % block_device

    if not is_block_device(bdev):
        juju_log('Failed to locate valid block device at %s' % bdev)
        raise CinderCharmError

    return bdev


def migrate_database():
    '''Runs cinder-manage to initialize a new database or migrate existing'''
    cmd = ['cinder-manage', 'db', 'sync']
    subprocess.check_call(cmd)


def ensure_ceph_pool(service, replicas):
    '''Creates a ceph pool for service if one does not exist'''
    # TODO: Ditto about moving somewhere sharable.
    if not ceph_pool_exists(service=service, name=service):
        ceph_create_pool(service=service, name=service, replicas=replicas)


def set_ceph_env_variables(service):
    # XXX: Horrid kludge to make cinder-volume use
    # a different ceph username than admin
    env = open('/etc/environment', 'r').read()
    if 'CEPH_ARGS' not in env:
        with open('/etc/environment', 'a') as out:
            out.write('CEPH_ARGS="--id %s"\n' % service)
    with open('/etc/init/cinder-volume.override', 'w') as out:
            out.write('env CEPH_ARGS="--id %s"\n' % service)


def do_openstack_upgrade(configs):
    """
    Perform an uprade of cinder.  Takes care of upgrading packages, rewriting
    configs + database migration and potentially any other post-upgrade
    actions.

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
    apt_install(packages=determine_packages(), options=dpkg_opts, fatal=True)

    # set CONFIGS to load templates from new release and regenerate config
    configs.set_release(openstack_release=new_os_rel)
    configs.write_all()

    if eligible_leader(CLUSTER_RES):
        migrate_database()
