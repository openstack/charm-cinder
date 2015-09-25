import os
import shutil
import subprocess
import uuid

from collections import OrderedDict
from copy import copy

from charmhelpers.contrib.python.packages import (
    pip_install,
)

from charmhelpers.core.hookenv import (
    charm_dir,
    config,
    local_unit,
    relation_get,
    relation_set,
    relation_ids,
    log,
    DEBUG,
    service_name,
    status_get,
)

from charmhelpers.fetch import (
    apt_upgrade,
    apt_update,
    apt_install,
    add_source
)

from charmhelpers.core.host import (
    adduser,
    add_group,
    add_user_to_group,
    lsb_release,
    mkdir,
    mounts,
    umount,
    service_restart,
    service_stop,
    service_start,
    write_file,
)

from charmhelpers.contrib.openstack.alternatives import install_alternative
from charmhelpers.contrib.hahelpers.cluster import (
    is_elected_leader,
    get_hacluster_config,
)

from charmhelpers.contrib.storage.linux.utils import (
    is_block_device,
    zap_disk,
    is_device_mounted
)

from charmhelpers.contrib.storage.linux.lvm import (
    create_lvm_physical_volume,
    create_lvm_volume_group,
    deactivate_lvm_volume_group,
    is_lvm_physical_volume,
    remove_lvm_physical_volume,
    list_lvm_volume_group
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
    git_install_requested,
    git_clone_and_install,
    git_src_dir,
    git_yaml_value,
    git_pip_venv_dir,
    os_release,
    set_os_workload_status,
)

from charmhelpers.core.decorators import (
    retry_on_exception,
)
from charmhelpers.core.templating import render

import cinder_contexts

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
]

API_PACKAGES = ['cinder-api']
VOLUME_PACKAGES = ['cinder-volume']
SCHEDULER_PACKAGES = ['cinder-scheduler']

BASE_GIT_PACKAGES = [
    'libffi-dev',
    'libmysqlclient-dev',
    'libssl-dev',
    'libxml2-dev',
    'libxslt1-dev',
    'libyaml-dev',
    'lvm2',
    'python-dev',
    'python-pip',
    'python-setuptools',
    'zlib1g-dev',
]

# ubuntu packages that should not be installed when deploying from source
GIT_PACKAGE_BLACKLIST = [
    'cinder-api',
    'cinder-common',
    'cinder-scheduler',
    'cinder-volume',
    'python-keystoneclient',
]

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
CHARM_CEPH_CONF = '/var/lib/charm/{}/ceph.conf'

HAPROXY_CONF = '/etc/haproxy/haproxy.cfg'
APACHE_SITE_CONF = '/etc/apache2/sites-available/openstack_https_frontend'
APACHE_SITE_24_CONF = '/etc/apache2/sites-available/' \
    'openstack_https_frontend.conf'

TEMPLATES = 'templates/'

# The interface is said to be satisfied if anyone of the interfaces in
# the
# list has a complete context.
REQUIRED_INTERFACES = {
    'database': ['shared-db', 'pgsql-db'],
    'message': ['amqp'],
    'identity': ['identity-service'],
}


def ceph_config_file():
    return CHARM_CEPH_CONF.format(service_name())

# Map config files to hook contexts and services that will be associated
# with file in restart_on_changes()'s service map.
CONFIG_FILES = OrderedDict([
    (CINDER_CONF, {
        'hook_contexts': [context.SharedDBContext(ssl_dir=CINDER_CONF_DIR),
                          context.PostgresqlDBContext(),
                          context.AMQPContext(ssl_dir=CINDER_CONF_DIR),
                          context.ImageServiceContext(),
                          context.OSConfigFlagContext(),
                          context.SyslogContext(),
                          cinder_contexts.CephContext(),
                          cinder_contexts.HAProxyContext(),
                          cinder_contexts.ImageServiceContext(),
                          cinder_contexts.CinderSubordinateConfigContext(
                              interface='storage-backend',
                              service='cinder',
                              config_file=CINDER_CONF),
                          cinder_contexts.StorageBackendContext(),
                          cinder_contexts.LoggingConfigContext(),
                          context.IdentityServiceContext(
                              service='cinder',
                              service_user='cinder'),
                          context.BindHostContext(),
                          context.WorkerConfigContext()],
        'services': ['cinder-api', 'cinder-volume',
                     'cinder-scheduler', 'haproxy']
    }),
    (CINDER_API_CONF, {
        'hook_contexts': [context.IdentityServiceContext()],
        'services': ['cinder-api'],
    }),
    (ceph_config_file(), {
        'hook_contexts': [context.CephContext()],
        'services': ['cinder-volume']
    }),
    (HAPROXY_CONF, {
        'hook_contexts': [context.HAProxyContext(singlenode_mode=True),
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
    """Register config files with their respective contexts.
    Regstration of some configs may not be required depending on
    existing of certain relations.
    """
    # if called without anything installed (eg during install hook)
    # just default to earliest supported release. configs dont get touched
    # till post-install, anyway.
    release = os_release('cinder-common', base='folsom')
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)

    confs = [CINDER_API_CONF,
             CINDER_CONF,
             HAPROXY_CONF]

    if relation_ids('ceph'):
        # need to create this early, new peers will have a relation during
        # registration # before they've run the ceph hooks to create the
        # directory.
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
        confs.append(ceph_config_file())

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
    '''Determine list of packages required for the currently enabled services.

    :returns: list of package names
    '''
    pkgs = copy(COMMON_PACKAGES)
    for s, p in [('api', API_PACKAGES),
                 ('volume', VOLUME_PACKAGES),
                 ('scheduler', SCHEDULER_PACKAGES)]:
        if service_enabled(s):
            pkgs += p

    if git_install_requested():
        pkgs.extend(BASE_GIT_PACKAGES)
        # don't include packages that will be installed from git
        for p in GIT_PACKAGE_BLACKLIST:
            pkgs.remove(p)

    return pkgs


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
                devices.append(ensure_loopback_device(block_device, size))

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
        return (bdev, size)
    else:
        return ('/dev/{}'.format(block_device), 0)


def check_db_initialised():
    """Check if we have received db init'd notify and restart services if we
    have not already.
    """
    settings = relation_get() or {}
    if settings:
        init_id = settings.get(CINDER_DB_INIT_RKEY)
        echoed_init_id = relation_get(unit=local_unit(),
                                      attribute=CINDER_DB_INIT_ECHO_RKEY)
        if (init_id and init_id != echoed_init_id and
                local_unit() not in init_id):
            log("Restarting cinder services following db initialisation",
                level=DEBUG)
            for svc in enabled_services():
                service_restart(svc)

            # Set echo
            relation_set(**{CINDER_DB_INIT_ECHO_RKEY: init_id})


# NOTE(jamespage): Retry deals with sync issues during one-shot HA deploys.
#                  mysql might be restarting or suchlike.
@retry_on_exception(5, base_delay=3, exc_type=subprocess.CalledProcessError)
def migrate_database():
    'Runs cinder-manage to initialize a new database or migrate existing'
    cmd = ['cinder-manage', 'db', 'sync']
    subprocess.check_call(cmd)
    # Notify peers so that services get restarted
    log("Notifying peer(s) that db is initialised and restarting services",
        level=DEBUG)
    for r_id in relation_ids('cluster'):
        for svc in enabled_services():
            service_restart(svc)

        id = "%s-%s" % (local_unit(), uuid.uuid4())
        relation_set(relation_id=r_id, **{CINDER_DB_INIT_RKEY: id})


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
    apt_install(determine_packages(), fatal=True)

    # set CONFIGS to load templates from new release and regenerate config
    configs.set_release(openstack_release=new_os_rel)
    configs.write_all()

    # Stop/start services and migrate DB if leader
    [service_stop(s) for s in services()]
    if is_elected_leader(CLUSTER_RES):
        migrate_database()
    [service_start(s) for s in services()]


def setup_ipv6():
    ubuntu_rel = lsb_release()['DISTRIB_CODENAME'].lower()
    if ubuntu_rel < "trusty":
        raise Exception("IPv6 is not supported in the charms for Ubuntu "
                        "versions less than Trusty 14.04")

    # NOTE(xianghui): Need to install haproxy(1.5.3) from trusty-backports
    # to support ipv6 address, so check is required to make sure not
    # breaking other versions, IPv6 only support for >= Trusty
    if ubuntu_rel == 'trusty':
        add_source('deb http://archive.ubuntu.com/ubuntu trusty-backports'
                   ' main')
        apt_update()
        apt_install('haproxy/trusty-backports', fatal=True)


def git_install(projects_yaml):
    """Perform setup, and install git repos specified in yaml parameter."""
    if git_install_requested():
        git_pre_install()
        git_clone_and_install(projects_yaml, core_project='cinder')
        git_post_install(projects_yaml)


def git_pre_install():
    """Perform cinder pre-install setup."""
    dirs = [{'path': '/etc/tgt',
             'owner': 'cinder',
             'group': 'cinder',
             'perms': 0750,
             },
            {'path': '/var/lib/cinder',
             'owner': 'cinder',
             'group': 'cinder',
             'perms': 0755,
             },
            {'path': '/var/lib/cinder/volumes',
             'owner': 'cinder',
             'group': 'cinder',
             'perms': 0750,
             },
            {'path': '/var/lock/cinder',
             'owner': 'cinder',
             'group': 'root',
             'perms': 0750,
             },
            {'path': '/var/log/cinder',
             'owner': 'cinder',
             'group': 'cinder',
             'perms': 0750,
             }]

    logs = [
        '/var/log/cinder/cinder-api.log',
        '/var/log/cinder/cinder-backup.log',
        '/var/log/cinder/cinder-scheduler.log',
        '/var/log/cinder/cinder-volume.log',
    ]

    adduser('cinder', shell='/bin/bash', system_user=True)
    add_group('cinder', system_group=True)
    add_user_to_group('cinder', 'cinder')

    for d in dirs:
        mkdir(d['path'], owner=d['owner'], group=d['group'], perms=d['perms'],
              force=False)

    for l in logs:
        write_file(l, '', owner='cinder', group='cinder', perms=0600)


def git_post_install(projects_yaml):
    """Perform cinder post-install setup."""
    http_proxy = git_yaml_value(projects_yaml, 'http_proxy')
    base_packages = ['mysql-python']
    for pkg in base_packages:
        if http_proxy:
            pip_install(pkg, proxy=http_proxy,
                        venv=git_pip_venv_dir(projects_yaml))
        else:
            pip_install(pkg,
                        venv=git_pip_venv_dir(projects_yaml))

    src_etc = os.path.join(git_src_dir(projects_yaml, 'cinder'), 'etc/cinder')
    configs = {
        'src': src_etc,
        'dest': '/etc/cinder',
    }

    if os.path.exists(configs['dest']):
        shutil.rmtree(configs['dest'])
    shutil.copytree(configs['src'], configs['dest'])

    # NOTE(coreycb): Need to find better solution than bin symlinks.
    symlinks = [
        {'src': os.path.join(git_pip_venv_dir(projects_yaml),
                             'bin/cinder-manage'),
         'link': '/usr/local/bin/cinder-manage'},
        {'src': os.path.join(git_pip_venv_dir(projects_yaml),
                             'bin/cinder-rootwrap'),
         'link': '/usr/local/bin/cinder-rootwrap'},
        # NOTE(coreycb): This is ugly but couldn't find pypi package that
        #                installs rbd.py and rados.py.
        {'src': '/usr/lib/python2.7/dist-packages/rbd.py',
         'link': os.path.join(git_pip_venv_dir(projects_yaml),
                              'lib/python2.7/site-packages/rbd.py')},
        {'src': '/usr/lib/python2.7/dist-packages/rados.py',
         'link': os.path.join(git_pip_venv_dir(projects_yaml),
                              'lib/python2.7/site-packages/rados.py')},
    ]

    for s in symlinks:
        if os.path.lexists(s['link']):
            os.remove(s['link'])
        os.symlink(s['src'], s['link'])

    render('cinder.conf', '/etc/cinder/cinder.conf', {}, owner='cinder',
           group='cinder', perms=0o644)
    render('git/cinder_tgt.conf', '/etc/tgt/conf.d', {}, owner='cinder',
           group='cinder', perms=0o644)
    render('git/logging.conf', '/etc/cinder/logging.conf', {}, owner='cinder',
           group='cinder', perms=0o644)
    render('git/cinder_sudoers', '/etc/sudoers.d/cinder_sudoers', {},
           owner='root', group='root', perms=0o440)

    os.chmod('/etc/sudoers.d', 0o750)

    bin_dir = os.path.join(git_pip_venv_dir(projects_yaml), 'bin')
    cinder_api_context = {
        'service_description': 'Cinder API server',
        'service_name': 'Cinder',
        'user_name': 'cinder',
        'start_dir': '/var/lib/cinder',
        'process_name': 'cinder-api',
        'executable_name': os.path.join(bin_dir, 'cinder-api'),
        'config_files': ['/etc/cinder/cinder.conf'],
        'log_file': '/var/log/cinder/cinder-api.log',
    }

    cinder_backup_context = {
        'service_description': 'Cinder backup server',
        'service_name': 'Cinder',
        'user_name': 'cinder',
        'start_dir': '/var/lib/cinder',
        'process_name': 'cinder-backup',
        'executable_name': os.path.join(bin_dir, 'cinder-backup'),
        'config_files': ['/etc/cinder/cinder.conf'],
        'log_file': '/var/log/cinder/cinder-backup.log',
    }

    cinder_scheduler_context = {
        'service_description': 'Cinder scheduler server',
        'service_name': 'Cinder',
        'user_name': 'cinder',
        'start_dir': '/var/lib/cinder',
        'process_name': 'cinder-scheduler',
        'executable_name': os.path.join(bin_dir, 'cinder-scheduler'),
        'config_files': ['/etc/cinder/cinder.conf'],
        'log_file': '/var/log/cinder/cinder-scheduler.log',
    }

    cinder_volume_context = {
        'service_description': 'Cinder volume server',
        'service_name': 'Cinder',
        'user_name': 'cinder',
        'start_dir': '/var/lib/cinder',
        'process_name': 'cinder-volume',
        'executable_name': os.path.join(bin_dir, 'cinder-volume'),
        'config_files': ['/etc/cinder/cinder.conf'],
        'log_file': '/var/log/cinder/cinder-volume.log',
    }

    # NOTE(coreycb): Needs systemd support
    templates_dir = 'hooks/charmhelpers/contrib/openstack/templates'
    templates_dir = os.path.join(charm_dir(), templates_dir)
    render('git.upstart', '/etc/init/cinder-api.conf',
           cinder_api_context, perms=0o644, templates_dir=templates_dir)
    render('git.upstart', '/etc/init/cinder-backup.conf',
           cinder_backup_context, perms=0o644, templates_dir=templates_dir)
    render('git.upstart', '/etc/init/cinder-scheduler.conf',
           cinder_scheduler_context, perms=0o644, templates_dir=templates_dir)
    render('git.upstart', '/etc/init/cinder-volume.conf',
           cinder_volume_context, perms=0o644, templates_dir=templates_dir)

    service_restart('tgtd')

    [service_restart(s) for s in services()]


def filesystem_mounted(fs):
    return subprocess.call(['grep', '-wqs', fs, '/proc/mounts']) == 0


def check_optional_relations(configs):
    required_interfaces = {}
    if relation_ids('ha'):
        required_interfaces['ha'] = ['cluster']
        try:
            get_hacluster_config()
        except:
            return ('blocked',
                    'hacluster missing configuration: '
                    'vip, vip_iface, vip_cidr')

    if relation_ids('ceph'):
        required_interfaces['storage-backend'] = ['ceph']

    if relation_ids('image-service'):
        required_interfaces['image'] = ['image-service']

    # Storage-backend?

    if required_interfaces:
        set_os_workload_status(configs, required_interfaces)
        return status_get()
    else:
        return 'unknown', 'No optional relations'
