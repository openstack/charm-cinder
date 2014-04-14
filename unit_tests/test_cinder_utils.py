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
    'ceph_create_pool',
    'ceph_pool_exists',
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
    'get_os_codename_package',
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
    def test_determine_packages_all(self, service_enabled):
        'It determines all packages required when all services enabled'
        service_enabled.return_value = True
        pkgs = cinder_utils.determine_packages()
        self.assertEquals(sorted(pkgs),
                          sorted(cinder_utils.COMMON_PACKAGES +
                                 cinder_utils.VOLUME_PACKAGES +
                                 cinder_utils.API_PACKAGES +
                                 cinder_utils.SCHEDULER_PACKAGES))

    @patch('cinder_utils.service_enabled')
    def test_determine_packages_subset(self, service_enabled):
        'It determines packages required for a subset of enabled services'
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

    @patch.object(cinder_utils, 'lvm_zap_disk')
    def test_clean_storage_unmount(self, zap_disk):
        'It unmounts block device when cleaning storage'
        self.is_lvm_physical_volume.return_value = False
        self.mounts.return_value = MOUNTS
        cinder_utils.clean_storage('/dev/vdb')
        self.umount.called_with('/dev/vdb', True)
        zap_disk.assert_called_with('/dev/vdb')

    @patch.object(cinder_utils, 'lvm_zap_disk')
    def test_clean_storage_zap_disk(self, zap_disk):
        'It removes traces of LVM when cleaning storage'
        self.mounts.return_value = []
        self.is_lvm_physical_volume.return_value = False
        cinder_utils.clean_storage('/dev/vdb')
        zap_disk.assert_called_with('/dev/vdb')

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

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage(self, extend_lvm, clean_storage):
        devices = ['/dev/vdb', '/dev/vdc']
        self.is_lvm_physical_volume.return_value = False
        cinder_utils.configure_lvm_storage(devices, 'test', True)
        clean_storage.assert_has_calls(
            [call('/dev/vdb'),
             call('/dev/vdc')]
        )
        self.create_lvm_physical_volume.assert_has_calls(
            [call('/dev/vdb'),
             call('/dev/vdc')]
        )
        self.create_lvm_volume_group.assert_called_with('test', '/dev/vdb')
        extend_lvm.assert_called_with('test', '/dev/vdc')

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_loopback(self, extend_lvm, clean_storage):
        devices = ['/mnt/loop0|10']
        self.ensure_loopback_device.return_value = '/dev/loop0'
        self.is_lvm_physical_volume.return_value = False
        cinder_utils.configure_lvm_storage(devices, 'test', True)
        clean_storage.assert_called_with('/dev/loop0')
        self.ensure_loopback_device.assert_called_with('/mnt/loop0', '10')
        self.create_lvm_physical_volume.assert_called_with('/dev/loop0')
        self.create_lvm_volume_group.assert_called_with('test', '/dev/loop0')
        self.assertFalse(extend_lvm.called)

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_existing_vg(self, extend_lvm,
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
        cinder_utils.configure_lvm_storage(devices, 'test', True)
        clean_storage.assert_has_calls(
            [call('/dev/vdc')]
        )
        self.create_lvm_physical_volume.assert_has_calls(
            [call('/dev/vdc')]
        )
        extend_lvm.assert_called_with('test', '/dev/vdc')
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_different_vg(self, extend_lvm,
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
        cinder_utils.configure_lvm_storage(devices, 'test', True)
        clean_storage.assert_called_with('/dev/vdc')
        self.create_lvm_physical_volume.assert_called_with('/dev/vdc')
        extend_lvm.assert_called_with('test', '/dev/vdc')
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_different_vg_ignore(self, extend_lvm,
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
        cinder_utils.configure_lvm_storage(devices, 'test', False)
        self.assertFalse(clean_storage.called)
        self.assertFalse(self.create_lvm_physical_volume.called)
        self.assertFalse(extend_lvm.called)
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch('subprocess.check_call')
    def test_extend_lvm_volume_group(self, _call):
        cinder_utils.extend_lvm_volume_group('test', '/dev/sdb')
        _call.assert_called_with(['vgextend', 'test', '/dev/sdb'])

    def test_migrate_database(self):
        'It migrates database with cinder-manage'
        with patch('subprocess.check_call') as check_call:
            cinder_utils.migrate_database()
            check_call.assert_called_with(['cinder-manage', 'db', 'sync'])

    def test_ensure_ceph_pool(self):
        self.ceph_pool_exists.return_value = False
        cinder_utils.ensure_ceph_pool(service='cinder', replicas=3)
        self.ceph_create_pool.assert_called_with(service='cinder',
                                                 name='cinder',
                                                 replicas=3)

    def test_ensure_ceph_pool_already_exists(self):
        self.ceph_pool_exists.return_value = True
        cinder_utils.ensure_ceph_pool(service='cinder', replicas=3)
        self.assertFalse(self.ceph_create_pool.called)

    @patch('os.path.exists')
    def test_register_configs_apache(self, exists):
        exists.return_value = False
        self.get_os_codename_package.return_value = 'grizzly'
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
        self.get_os_codename_package.return_value = 'grizzly'
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
        self.get_os_codename_package.return_value = 'grizzly'
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
