from mock import patch, call, MagicMock

from collections import OrderedDict
import os

os.environ['JUJU_UNIT_NAME'] = 'cinder'
import cinder_utils as cinder_utils

from test_utils import (
    CharmTestCase,
)

TO_PATCH = [
    # helpers.core.hookenv
    'config',
    'log',
    # helpers.core.host
    'mounts',
    'umount',
    'mkdir',
    # ceph utils
    # storage_utils
    'create_lvm_physical_volume',
    'create_lvm_volume_group',
    'deactivate_lvm_volume_group',
    'is_lvm_physical_volume',
    'list_lvm_volume_group',
    'relation_ids',
    'remove_lvm_physical_volume',
    'ensure_loopback_device',
    'is_block_device',
    'zap_disk',
    'os_release',
    'get_os_codename_install_source',
    'configure_installation_source',
    'eligible_leader',
    'templating',
    'install_alternative',
    # fetch
    'apt_update',
    'apt_upgrade',
    'apt_install',
    'service_stop',
    'service_start',
    # cinder
    'ceph_config_file'
]


MOUNTS = [
    ['/mnt', '/dev/vdb']
]

DPKG_OPTIONS = [
    '--option', 'Dpkg::Options::=--force-confnew',
    '--option', 'Dpkg::Options::=--force-confdef',
]

FDISKDISPLAY = """
  Disk /dev/vdb doesn't contain a valid partition table

  Disk /dev/vdb: 21.5 GB, 21474836480 bytes
  16 heads, 63 sectors/track, 41610 cylinders, total 41943040 sectors
  Units = sectors of 1 * 512 = 512 bytes
  Sector size (logical/physical): 512 bytes / 512 bytes
  I/O size (minimum/optimal): 512 bytes / 512 bytes
  Disk identifier: 0x00000000

"""

openstack_origin_git = \
    """repositories:
         - {name: requirements,
            repository: 'git://git.openstack.org/openstack/requirements',
            branch: stable/juno}
         - {name: cinder,
            repository: 'git://git.openstack.org/openstack/cinder',
            branch: stable/juno}"""


class TestCinderUtils(CharmTestCase):

    def setUp(self):
        super(TestCinderUtils, self).setUp(cinder_utils, TO_PATCH)
        self.config.side_effect = self.test_config.get_all

    def svc_enabled(self, svc):
        return svc in self.test_config.get('enabled-services')

    def test_all_services_enabled(self):
        'It determines all services are enabled based on config'
        self.test_config.set('enabled-services', 'all')
        enabled = []
        for s in ['volume', 'api', 'scheduler']:
            enabled.append(cinder_utils.service_enabled(s))
        self.assertEquals(enabled, [True, True, True])

    def test_service_enabled(self):
        'It determines services are enabled based on config'
        self.test_config.set('enabled-services', 'api,volume,scheduler')
        self.assertTrue(cinder_utils.service_enabled('volume'))

    def test_service_not_enabled(self):
        'It determines services are not enabled based on config'
        self.test_config.set('enabled-services', 'api,scheduler')
        self.assertFalse(cinder_utils.service_enabled('volume'))

    @patch('cinder_utils.service_enabled')
    @patch('cinder_utils.git_install_requested')
    def test_determine_packages_all(self, git_requested, service_enabled):
        'It determines all packages required when all services enabled'
        git_requested.return_value = False
        service_enabled.return_value = True
        pkgs = cinder_utils.determine_packages()
        self.assertEquals(sorted(pkgs),
                          sorted(cinder_utils.COMMON_PACKAGES +
                                 cinder_utils.VOLUME_PACKAGES +
                                 cinder_utils.API_PACKAGES +
                                 cinder_utils.SCHEDULER_PACKAGES))

    @patch('cinder_utils.service_enabled')
    @patch('cinder_utils.git_install_requested')
    def test_determine_packages_subset(self, git_requested, service_enabled):
        'It determines packages required for a subset of enabled services'
        git_requested.return_value = False
        service_enabled.side_effect = self.svc_enabled

        self.test_config.set('enabled-services', 'api')
        pkgs = cinder_utils.determine_packages()
        common = cinder_utils.COMMON_PACKAGES
        self.assertEquals(sorted(pkgs),
                          sorted(common + cinder_utils.API_PACKAGES))
        self.test_config.set('enabled-services', 'volume')
        pkgs = cinder_utils.determine_packages()
        common = cinder_utils.COMMON_PACKAGES
        self.assertEquals(sorted(pkgs),
                          sorted(common + cinder_utils.VOLUME_PACKAGES))
        self.test_config.set('enabled-services', 'api,scheduler')
        pkgs = cinder_utils.determine_packages()
        common = cinder_utils.COMMON_PACKAGES
        self.assertEquals(sorted(pkgs),
                          sorted(common + cinder_utils.API_PACKAGES +
                                 cinder_utils.SCHEDULER_PACKAGES))

    def test_services(self):
        self.assertEquals(cinder_utils.services(),
                          ['haproxy', 'apache2', 'cinder-api',
                           'cinder-volume', 'cinder-scheduler'])

    def test_creates_restart_map_all_enabled(self):
        'It creates correct restart map when all services enabled'
        ex_map = OrderedDict([
            ('/etc/cinder/cinder.conf', ['cinder-api', 'cinder-volume',
                                         'cinder-scheduler', 'haproxy']),
            ('/etc/cinder/api-paste.ini', ['cinder-api']),
            ('/var/lib/charm/cinder/ceph.conf', ['cinder-volume']),
            ('/etc/haproxy/haproxy.cfg', ['haproxy']),
            ('/etc/apache2/sites-available/openstack_https_frontend',
             ['apache2']),
            ('/etc/apache2/sites-available/openstack_https_frontend.conf',
             ['apache2']),
        ])
        self.assertEquals(cinder_utils.restart_map(), ex_map)

    @patch('cinder_utils.service_enabled')
    def test_creates_restart_map_no_api(self, service_enabled):
        'It creates correct restart map with api disabled'
        service_enabled.side_effect = self.svc_enabled
        self.test_config.set('enabled-services', 'scheduler,volume')
        ex_map = OrderedDict([
            ('/etc/cinder/cinder.conf', ['cinder-volume', 'cinder-scheduler',
                                         'haproxy']),
            ('/var/lib/charm/cinder/ceph.conf', ['cinder-volume']),
            ('/etc/haproxy/haproxy.cfg', ['haproxy']),
            ('/etc/apache2/sites-available/openstack_https_frontend',
             ['apache2']),
            ('/etc/apache2/sites-available/openstack_https_frontend.conf',
             ['apache2']),
        ])
        self.assertEquals(cinder_utils.restart_map(), ex_map)

    @patch('cinder_utils.service_enabled')
    def test_creates_restart_map_only_api(self, service_enabled):
        'It creates correct restart map with only api enabled'
        service_enabled.side_effect = self.svc_enabled
        self.test_config.set('enabled-services', 'api')
        ex_map = OrderedDict([
            ('/etc/cinder/cinder.conf', ['cinder-api', 'haproxy']),
            ('/etc/cinder/api-paste.ini', ['cinder-api']),
            ('/etc/haproxy/haproxy.cfg', ['haproxy']),
            ('/etc/apache2/sites-available/openstack_https_frontend',
             ['apache2']),
            ('/etc/apache2/sites-available/openstack_https_frontend.conf',
             ['apache2']),
        ])
        self.assertEquals(cinder_utils.restart_map(), ex_map)

    def test_clean_storage_unmount(self):
        'It unmounts block device when cleaning storage'
        self.is_lvm_physical_volume.return_value = False
        self.zap_disk.return_value = True
        self.mounts.return_value = MOUNTS
        cinder_utils.clean_storage('/dev/vdb')
        self.umount.called_with('/dev/vdb', True)

    def test_clean_storage_lvm_wipe(self):
        'It removes traces of LVM when cleaning storage'
        self.mounts.return_value = []
        self.is_lvm_physical_volume.return_value = True
        cinder_utils.clean_storage('/dev/vdb')
        self.remove_lvm_physical_volume.assert_called_with('/dev/vdb')
        self.deactivate_lvm_volume_group.assert_called_with('/dev/vdb')
        self.zap_disk.assert_called_with('/dev/vdb')

    def test_clean_storage_zap_disk(self):
        'It removes traces of LVM when cleaning storage'
        self.mounts.return_value = []
        self.is_lvm_physical_volume.return_value = False
        cinder_utils.clean_storage('/dev/vdb')
        self.zap_disk.assert_called_with('/dev/vdb')

    def test_parse_block_device(self):
        self.assertTrue(cinder_utils._parse_block_device(None),
                        (None, 0))
        self.assertTrue(cinder_utils._parse_block_device('vdc'),
                        ('/dev/vdc', 0))
        self.assertTrue(cinder_utils._parse_block_device('/dev/vdc'),
                        ('/dev/vdc', 0))
        self.assertTrue(cinder_utils._parse_block_device('/dev/vdc'),
                        ('/dev/vdc', 0))
        self.assertTrue(cinder_utils._parse_block_device('/mnt/loop0|10'),
                        ('/mnt/loop0', 10))
        self.assertTrue(cinder_utils._parse_block_device('/mnt/loop0'),
                        ('/mnt/loop0', cinder_utils.DEFAULT_LOOPBACK_SIZE))

    @patch('subprocess.check_output')
    def test_has_partition_table(self, _check):
        _check.return_value = FDISKDISPLAY
        block_device = '/dev/vdb'
        cinder_utils.has_partition_table(block_device)
        _check.assert_called_with(['fdisk', '-l', '/dev/vdb'], stderr=-2)

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage(self, extend_lvm, reduce_lvm,
                                   clean_storage):
        devices = ['/dev/vdb', '/dev/vdc']
        self.is_lvm_physical_volume.return_value = False
        cinder_utils.configure_lvm_storage(devices, 'test', True, True)
        clean_storage.assert_has_calls(
            [call('/dev/vdb'),
             call('/dev/vdc')]
        )
        self.create_lvm_physical_volume.assert_has_calls(
            [call('/dev/vdb'),
             call('/dev/vdc')]
        )
        self.create_lvm_volume_group.assert_called_with('test', '/dev/vdb')
        reduce_lvm.assert_called_with('test')
        extend_lvm.assert_called_with('test', '/dev/vdc')

    @patch.object(cinder_utils, 'has_partition_table')
    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_unused_dev(self, extend_lvm, reduce_lvm,
                                              clean_storage, has_part):
        devices = ['/dev/vdb', '/dev/vdc']
        self.is_lvm_physical_volume.return_value = False
        has_part.return_value = False
        cinder_utils.configure_lvm_storage(devices, 'test', False, True)
        clean_storage.assert_has_calls(
            [call('/dev/vdb'),
             call('/dev/vdc')]
        )
        self.create_lvm_physical_volume.assert_has_calls(
            [call('/dev/vdb'),
             call('/dev/vdc')]
        )
        self.create_lvm_volume_group.assert_called_with('test', '/dev/vdb')
        reduce_lvm.assert_called_with('test')
        extend_lvm.assert_called_with('test', '/dev/vdc')

    @patch.object(cinder_utils, 'has_partition_table')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    def test_configure_lvm_storage_used_dev(self, reduce_lvm, has_part):
        devices = ['/dev/vdb', '/dev/vdc']
        self.is_lvm_physical_volume.return_value = False
        has_part.return_value = True
        cinder_utils.configure_lvm_storage(devices, 'test', False, True)
        reduce_lvm.assert_called_with('test')

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_loopback(self, extend_lvm, reduce_lvm,
                                            clean_storage):
        devices = ['/mnt/loop0|10']
        self.ensure_loopback_device.return_value = '/dev/loop0'
        self.is_lvm_physical_volume.return_value = False
        cinder_utils.configure_lvm_storage(devices, 'test', True, True)
        clean_storage.assert_called_with('/dev/loop0')
        self.ensure_loopback_device.assert_called_with('/mnt/loop0', '10')
        self.create_lvm_physical_volume.assert_called_with('/dev/loop0')
        self.create_lvm_volume_group.assert_called_with('test', '/dev/loop0')
        reduce_lvm.assert_called_with('test')
        self.assertFalse(extend_lvm.called)

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_existing_vg(self, extend_lvm, reduce_lvm,
                                               clean_storage):
        def pv_lookup(device):
            devices = {
                '/dev/vdb': True,
                '/dev/vdc': False
            }
            return devices[device]

        def vg_lookup(device):
            devices = {
                '/dev/vdb': 'test',
                '/dev/vdc': None
            }
            return devices[device]
        devices = ['/dev/vdb', '/dev/vdc']
        self.is_lvm_physical_volume.side_effect = pv_lookup
        self.list_lvm_volume_group.side_effect = vg_lookup
        cinder_utils.configure_lvm_storage(devices, 'test', True, True)
        clean_storage.assert_has_calls(
            [call('/dev/vdc')]
        )
        self.create_lvm_physical_volume.assert_has_calls(
            [call('/dev/vdc')]
        )
        reduce_lvm.assert_called_with('test')
        extend_lvm.assert_called_with('test', '/dev/vdc')
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_different_vg(self, extend_lvm, reduce_lvm,
                                                clean_storage):
        def pv_lookup(device):
            devices = {
                '/dev/vdb': True,
                '/dev/vdc': True
            }
            return devices[device]

        def vg_lookup(device):
            devices = {
                '/dev/vdb': 'test',
                '/dev/vdc': 'another'
            }
            return devices[device]
        devices = ['/dev/vdb', '/dev/vdc']
        self.is_lvm_physical_volume.side_effect = pv_lookup
        self.list_lvm_volume_group.side_effect = vg_lookup
        cinder_utils.configure_lvm_storage(devices, 'test', True, True)
        clean_storage.assert_called_with('/dev/vdc')
        self.create_lvm_physical_volume.assert_called_with('/dev/vdc')
        reduce_lvm.assert_called_with('test')
        extend_lvm.assert_called_with('test', '/dev/vdc')
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_different_vg_ignore(self, extend_lvm,
                                                       reduce_lvm,
                                                       clean_storage):
        def pv_lookup(device):
            devices = {
                '/dev/vdb': True,
                '/dev/vdc': True
            }
            return devices[device]

        def vg_lookup(device):
            devices = {
                '/dev/vdb': 'test',
                '/dev/vdc': 'another'
            }
            return devices[device]
        devices = ['/dev/vdb', '/dev/vdc']
        self.is_lvm_physical_volume.side_effect = pv_lookup
        self.list_lvm_volume_group.side_effect = vg_lookup
        cinder_utils.configure_lvm_storage(devices, 'test', False, False)
        self.assertFalse(clean_storage.called)
        self.assertFalse(self.create_lvm_physical_volume.called)
        self.assertFalse(reduce_lvm.called)
        self.assertFalse(extend_lvm.called)
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch('subprocess.check_call')
    def test_reduce_lvm_volume_group_missing(self, _call):
        cinder_utils.reduce_lvm_volume_group_missing('test')
        _call.assert_called_with(['vgreduce', '--removemissing', 'test'])

    @patch('subprocess.check_call')
    def test_extend_lvm_volume_group(self, _call):
        cinder_utils.extend_lvm_volume_group('test', '/dev/sdb')
        _call.assert_called_with(['vgextend', 'test', '/dev/sdb'])

    def test_migrate_database(self):
        'It migrates database with cinder-manage'
        with patch('subprocess.check_call') as check_call:
            cinder_utils.migrate_database()
            check_call.assert_called_with(['cinder-manage', 'db', 'sync'])

    @patch('os.path.exists')
    def test_register_configs_apache(self, exists):
        exists.return_value = False
        self.os_release.return_value = 'grizzly'
        self.relation_ids.return_value = False
        configs = cinder_utils.register_configs()
        calls = []
        for conf in [cinder_utils.CINDER_API_CONF,
                     cinder_utils.CINDER_CONF,
                     cinder_utils.APACHE_SITE_CONF,
                     cinder_utils.HAPROXY_CONF]:
            calls.append(
                call(conf,
                     cinder_utils.CONFIG_FILES[conf]['hook_contexts'])
            )
        configs.register.assert_has_calls(calls, any_order=True)

    @patch('os.path.exists')
    def test_register_configs_apache24(self, exists):
        exists.return_value = True
        self.os_release.return_value = 'grizzly'
        self.relation_ids.return_value = False
        configs = cinder_utils.register_configs()
        calls = []
        for conf in [cinder_utils.CINDER_API_CONF,
                     cinder_utils.CINDER_CONF,
                     cinder_utils.APACHE_SITE_24_CONF,
                     cinder_utils.HAPROXY_CONF]:
            calls.append(
                call(conf,
                     cinder_utils.CONFIG_FILES[conf]['hook_contexts'])
            )
        configs.register.assert_has_calls(calls, any_order=True)

    @patch('os.path.isdir')
    @patch('os.path.exists')
    def test_register_configs_ceph(self, exists, isdir):
        exists.return_value = True
        isdir.return_value = False
        self.os_release.return_value = 'grizzly'
        self.relation_ids.return_value = ['ceph:0']
        self.ceph_config_file.return_value = '/var/lib/charm/cinder/ceph.conf'
        configs = cinder_utils.register_configs()
        calls = []
        for conf in [cinder_utils.CINDER_API_CONF,
                     cinder_utils.CINDER_CONF,
                     cinder_utils.HAPROXY_CONF,
                     cinder_utils.ceph_config_file()]:
            calls.append(
                call(conf,
                     cinder_utils.CONFIG_FILES[conf]['hook_contexts'])
            )
        configs.register.assert_has_calls(calls, any_order=True)

    def test_set_ceph_kludge(self):
        pass
        """
        def set_ceph_env_variables(service):
            # XXX: Horrid kludge to make cinder-volume use
            # a different ceph username than admin
            env = open('/etc/environment', 'r').read()
            if 'CEPH_ARGS' not in env:
                with open('/etc/environment', 'a') as out:
                    out.write('CEPH_ARGS="--id %s"\n' % service)
            with open('/etc/init/cinder-volume.override', 'w') as out:
                    out.write('env CEPH_ARGS="--id %s"\n' % service)
        """

    @patch.object(cinder_utils, 'services')
    @patch.object(cinder_utils, 'migrate_database')
    @patch.object(cinder_utils, 'determine_packages')
    def test_openstack_upgrade_leader(self, pkgs, migrate, services):
        pkgs.return_value = ['mypackage']
        self.config.side_effect = None
        self.config.return_value = 'cloud:precise-havana'
        services.return_value = ['cinder-api', 'cinder-volume']
        self.eligible_leader.return_value = True
        self.get_os_codename_install_source.return_value = 'havana'
        configs = MagicMock()
        cinder_utils.do_openstack_upgrade(configs)
        self.assertTrue(configs.write_all.called)
        self.apt_upgrade.assert_called_with(options=DPKG_OPTIONS,
                                            fatal=True, dist=True)
        self.apt_install.assert_called_with(['mypackage'], fatal=True)
        configs.set_release.assert_called_with(openstack_release='havana')
        self.assertTrue(migrate.called)

    @patch.object(cinder_utils, 'services')
    @patch.object(cinder_utils, 'migrate_database')
    @patch.object(cinder_utils, 'determine_packages')
    def test_openstack_upgrade_not_leader(self, pkgs, migrate, services):
        pkgs.return_value = ['mypackage']
        self.config.side_effect = None
        self.config.return_value = 'cloud:precise-havana'
        services.return_value = ['cinder-api', 'cinder-volume']
        self.eligible_leader.return_value = False
        self.get_os_codename_install_source.return_value = 'havana'
        configs = MagicMock()
        cinder_utils.do_openstack_upgrade(configs)
        self.assertTrue(configs.write_all.called)
        self.apt_upgrade.assert_called_with(options=DPKG_OPTIONS,
                                            fatal=True, dist=True)
        self.apt_install.assert_called_with(['mypackage'], fatal=True)
        configs.set_release.assert_called_with(openstack_release='havana')
        self.assertFalse(migrate.called)

    @patch.object(cinder_utils, 'git_install_requested')
    @patch.object(cinder_utils, 'git_clone_and_install')
    @patch.object(cinder_utils, 'git_post_install')
    @patch.object(cinder_utils, 'git_pre_install')
    def test_git_install(self, git_pre, git_post, git_clone_and_install,
                         git_requested):
        projects_yaml = openstack_origin_git
        git_requested.return_value = True
        cinder_utils.git_install(projects_yaml)
        self.assertTrue(git_pre.called)
        git_clone_and_install.assert_called_with(openstack_origin_git,
                                                 core_project='cinder')
        self.assertTrue(git_post.called)

    @patch.object(cinder_utils, 'mkdir')
    @patch.object(cinder_utils, 'write_file')
    @patch.object(cinder_utils, 'add_user_to_group')
    @patch.object(cinder_utils, 'add_group')
    @patch.object(cinder_utils, 'adduser')
    def test_git_pre_install(self, adduser, add_group, add_user_to_group,
                             write_file, mkdir):
        cinder_utils.git_pre_install()
        adduser.assert_called_with('cinder', shell='/bin/bash',
                                   system_user=True)
        add_group.assert_called_with('cinder', system_group=True)
        add_user_to_group.assert_called_with('cinder', 'cinder')
        expected = [
            call('/etc/tgt', owner='cinder', perms=488, force=False,
                 group='cinder'),
            call('/var/lib/cinder', owner='cinder', perms=493, force=False,
                 group='cinder'),
            call('/var/lib/cinder/volumes', owner='cinder', perms=488,
                 force=False, group='cinder'),
            call('/var/lock/cinder', owner='cinder', perms=488, force=False,
                 group='root'),
            call('/var/log/cinder', owner='cinder', perms=488, force=False,
                 group='cinder'),
        ]
        self.assertEquals(mkdir.call_args_list, expected)
        expected = [
            call('/var/log/cinder/cinder-api.log', '', perms=0600,
                 owner='cinder', group='cinder'),
            call('/var/log/cinder/cinder-backup.log', '', perms=0600,
                 owner='cinder', group='cinder'),
            call('/var/log/cinder/cinder-scheduler.log', '', perms=0600,
                 owner='cinder', group='cinder'),
            call('/var/log/cinder/cinder-volume.log', '', perms=0600,
                 owner='cinder', group='cinder'),
        ]
        self.assertEquals(write_file.call_args_list, expected)

    @patch.object(cinder_utils, 'git_src_dir')
    @patch.object(cinder_utils, 'service_restart')
    @patch.object(cinder_utils, 'render')
    @patch.object(cinder_utils, 'pip_install')
    @patch('os.path.join')
    @patch('os.path.exists')
    @patch('shutil.copytree')
    @patch('shutil.rmtree')
    @patch('pwd.getpwnam')
    @patch('grp.getgrnam')
    @patch('os.chown')
    @patch('os.chmod')
    @patch('os.symlink')
    def test_git_post_install(self, symlink, chmod, chown, grp, pwd, rmtree,
                              copytree, exists, join, pip_install, render,
                              service_restart, git_src_dir):
        projects_yaml = openstack_origin_git
        join.return_value = 'joined-string'
        cinder_utils.git_post_install(projects_yaml)
        pip_install('mysql-python', venv='joined-string')
        expected = [
            call('joined-string', '/etc/cinder'),
        ]
        copytree.assert_has_calls(expected)

        expected = [
            call('joined-string', '/usr/local/bin/cinder-manage'),
        ]
        symlink.assert_has_calls(expected, any_order=True)

        cinder_api_context = {
            'service_description': 'Cinder API server',
            'service_name': 'Cinder',
            'user_name': 'cinder',
            'start_dir': '/var/lib/cinder',
            'process_name': 'cinder-api',
            'executable_name': 'joined-string',
            'config_files': ['/etc/cinder/cinder.conf'],
            'log_file': '/var/log/cinder/cinder-api.log',
        }

        cinder_backup_context = {
            'service_description': 'Cinder backup server',
            'service_name': 'Cinder',
            'user_name': 'cinder',
            'start_dir': '/var/lib/cinder',
            'process_name': 'cinder-backup',
            'executable_name': 'joined-string',
            'config_files': ['/etc/cinder/cinder.conf'],
            'log_file': '/var/log/cinder/cinder-backup.log',
        }

        cinder_scheduler_context = {
            'service_description': 'Cinder scheduler server',
            'service_name': 'Cinder',
            'user_name': 'cinder',
            'start_dir': '/var/lib/cinder',
            'process_name': 'cinder-scheduler',
            'executable_name': 'joined-string',
            'config_files': ['/etc/cinder/cinder.conf'],
            'log_file': '/var/log/cinder/cinder-scheduler.log',
        }

        cinder_volume_context = {
            'service_description': 'Cinder volume server',
            'service_name': 'Cinder',
            'user_name': 'cinder',
            'start_dir': '/var/lib/cinder',
            'process_name': 'cinder-volume',
            'executable_name': 'joined-string',
            'config_files': ['/etc/cinder/cinder.conf'],
            'log_file': '/var/log/cinder/cinder-volume.log',
        }
        expected = [
            call('cinder.conf', '/etc/cinder/cinder.conf', {}, owner='cinder',
                 group='cinder', perms=0o644),
            call('git/cinder_tgt.conf', '/etc/tgt/conf.d', {}, owner='cinder',
                 group='cinder', perms=0o644),
            call('git/logging.conf', '/etc/cinder/logging.conf', {},
                 owner='cinder', group='cinder', perms=0o644),
            call('git/cinder_sudoers', '/etc/sudoers.d/cinder_sudoers', {},
                 owner='root', group='root', perms=0o440),
            call('git.upstart', '/etc/init/cinder-api.conf',
                 cinder_api_context, perms=0o644,
                 templates_dir='joined-string'),
            call('git.upstart', '/etc/init/cinder-backup.conf',
                 cinder_backup_context, perms=0o644,
                 templates_dir='joined-string'),
            call('git.upstart', '/etc/init/cinder-scheduler.conf',
                 cinder_scheduler_context, perms=0o644,
                 templates_dir='joined-string'),
            call('git.upstart', '/etc/init/cinder-volume.conf',
                 cinder_volume_context, perms=0o644,
                 templates_dir='joined-string'),
        ]
        self.assertEquals(render.call_args_list, expected)
        expected = [
            call('tgtd'), call('haproxy'), call('apache2'),
            call('cinder-api'), call('cinder-volume'),
            call('cinder-scheduler'),
        ]
        self.assertEquals(service_restart.call_args_list, expected)
