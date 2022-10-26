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

import os
import subprocess

from collections import OrderedDict
from unittest.mock import patch, call, MagicMock, Mock

os.environ['JUJU_UNIT_NAME'] = 'cinder'
import cinder_utils as cinder_utils

from test_utils import CharmTestCase

TO_PATCH = [
    # helpers.core.hookenv
    'config',
    'log',
    'juju_log',
    'relation_get',
    'relation_set',
    'local_unit',
    # helpers.core.host
    'lsb_release',
    'mounts',
    'umount',
    'mkdir',
    'service_restart',
    # ceph utils
    # storage_utils
    'create_lvm_physical_volume',
    'create_lvm_volume_group',
    'deactivate_lvm_volume_group',
    'is_lvm_physical_volume',
    'list_lvm_volume_group',
    'relation_ids',
    'relation_set',
    'remove_lvm_physical_volume',
    'ensure_loopback_device',
    'is_block_device',
    'is_device_mounted',
    'zap_disk',
    'os_release',
    'get_os_codename_install_source',
    'configure_installation_source',
    'is_elected_leader',
    'templating',
    'install_alternative',
    'os_application_version_set',
    # fetch
    'apt_update',
    'apt_upgrade',
    'apt_install',
    'apt_purge',
    'apt_autoremove',
    'filter_missing_packages',
    'service_stop',
    'service_start',
    # cinder
    'ceph_config_file',
    'token_cache_pkgs',
    'enable_memcache',
]


MOUNTS = [
    ['/mnt', '/dev/fakevbd']
]

DPKG_OPTIONS = [
    '--option', 'Dpkg::Options::=--force-confnew',
    '--option', 'Dpkg::Options::=--force-confdef',
]

FDISKDISPLAY = """
  Disk /dev/fakevbd doesn't contain a valid partition table

  Disk /dev/fakevbd: 21.5 GB, 21474836480 bytes
  16 heads, 63 sectors/track, 41610 cylinders, total 41943040 sectors
  Units = sectors of 1 * 512 = 512 bytes
  Sector size (logical/physical): 512 bytes / 512 bytes
  I/O size (minimum/optimal): 512 bytes / 512 bytes
  Disk identifier: 0x00000000

"""


class TestCinderUtils(CharmTestCase):

    def setUp(self):
        super(TestCinderUtils, self).setUp(cinder_utils, TO_PATCH)
        self.config.side_effect = self.test_config.get_all
        self.apache24_conf_dir = '/etc/apache2/conf-available'
        self.charm_ceph_conf = '/var/lib/charm/cinder/ceph.conf'
        self.ceph_conf = '/etc/ceph/ceph.conf'
        self.cinder_conf = '/etc/cinder/cinder.conf'

    def svc_enabled(self, svc):
        return svc in self.test_config.get('enabled-services')

    def test_all_services_enabled(self):
        'It determines all services are enabled based on config'
        self.test_config.set('enabled-services', 'all')
        enabled = []
        for s in ['volume', 'api', 'scheduler']:
            enabled.append(cinder_utils.service_enabled(s))
        self.assertEqual(enabled, [True, True, True])

    def test_service_enabled(self):
        'It determines services are enabled based on config'
        self.test_config.set('enabled-services', 'api,volume,scheduler')
        self.assertTrue(cinder_utils.service_enabled('volume'))

    def test_service_not_enabled(self):
        'It determines services are not enabled based on config'
        self.test_config.set('enabled-services', 'api,scheduler')
        self.assertFalse(cinder_utils.service_enabled('volume'))

    @patch.object(cinder_utils, 'get_subordinate_release_packages')
    def test_determine_purge_packages(
            self,
            mock_get_subordinate_release_packages):
        'Ensure no packages are identified for purge prior to rocky'
        self.os_release.return_value = 'queens'
        self.assertEqual(cinder_utils.determine_purge_packages(), [])

    @patch.object(cinder_utils, 'get_subordinate_release_packages')
    def test_determine_purge_packages_rocky(
            self,
            mock_get_subordinate_release_packages):
        'Ensure python packages are identified for purge at rocky'
        self.os_release.return_value = 'rocky'
        self.assertEqual(cinder_utils.determine_purge_packages(),
                         sorted(set([p for p in cinder_utils.COMMON_PACKAGES
                                    if p.startswith('python-')] +
                                    ['python-cinder', 'python-memcache'])))

    @patch.object(cinder_utils, 'get_subordinate_release_packages')
    @patch('cinder_utils.service_enabled')
    def test_determine_packages_all(
            self,
            service_enabled,
            mock_get_subordinate_release_packages):
        'It determines all packages required when all services enabled'
        service_enabled.return_value = True
        self.os_release.return_value = 'icehouse'
        pkgs = cinder_utils.determine_packages()
        self.assertEqual(sorted(pkgs),
                         sorted(cinder_utils.COMMON_PACKAGES +
                                cinder_utils.VOLUME_PACKAGES +
                                cinder_utils.API_PACKAGES +
                                cinder_utils.SCHEDULER_PACKAGES))

    @patch.object(cinder_utils, 'get_subordinate_release_packages')
    @patch('cinder_utils.service_enabled')
    def test_determine_packages_all_rocky(
            self,
            service_enabled,
            mock_get_subordinate_release_packages):
        'Check python3 packages are installed @ rocky'
        service_enabled.return_value = True
        self.os_release.return_value = 'rocky'
        pkgs = cinder_utils.determine_packages()
        self.assertEqual(
            sorted(pkgs),
            sorted([p for p in cinder_utils.COMMON_PACKAGES
                    if not p.startswith('python-')] +
                   cinder_utils.VOLUME_PACKAGES +
                   cinder_utils.API_PACKAGES +
                   cinder_utils.SCHEDULER_PACKAGES +
                   cinder_utils.PY3_PACKAGES +
                   cinder_utils.PY3_API_PACKAGES))

    @patch.object(cinder_utils, 'get_subordinate_release_packages')
    @patch('cinder_utils.service_enabled')
    def test_determine_packages_subset(self, service_enabled,
                                       mock_get_subordinate_release_packages):
        'It determines packages required for a subset of enabled services'
        service_enabled.side_effect = self.svc_enabled
        self.test_config.set('openstack-origin', 'cloud:xenial-newton')
        self.os_release.return_value = 'newton'
        self.token_cache_pkgs.return_value = ['memcached']

        self.test_config.set('enabled-services', 'api')
        pkgs = cinder_utils.determine_packages()
        common = cinder_utils.COMMON_PACKAGES
        self.assertEqual(
            sorted(pkgs),
            sorted(common + cinder_utils.API_PACKAGES + ['memcached']))
        self.test_config.set('enabled-services', 'volume')
        pkgs = cinder_utils.determine_packages()
        common = cinder_utils.COMMON_PACKAGES
        self.assertEqual(
            sorted(pkgs),
            sorted(common + cinder_utils.VOLUME_PACKAGES + ['memcached']))
        self.test_config.set('enabled-services', 'api,scheduler')
        pkgs = cinder_utils.determine_packages()
        common = cinder_utils.COMMON_PACKAGES
        self.assertEqual(
            sorted(pkgs),
            sorted(common + cinder_utils.API_PACKAGES + ['memcached'] +
                   cinder_utils.SCHEDULER_PACKAGES))

    @patch('cinder_utils.restart_map')
    def test_services(self, restart_map):
        restart_map.return_value = OrderedDict([
            ('test_conf1', ['svc1']),
            ('test_conf2', ['svc2', 'svc3', 'svc1']),
        ])
        self.assertEqual(cinder_utils.services(), ['svc1', 'svc2', 'svc3'])

    @patch('cinder_utils.service_enabled')
    @patch('os.path.exists')
    def test_creates_resource_map_all_enabled(self, path_exists,
                                              service_enabled):
        service_enabled.return_value = True
        path_exists.return_value = True
        self.os_release.return_value = 'havana'
        self.ceph_config_file.return_value = self.charm_ceph_conf
        self.relation_ids.return_value = []
        self.enable_memcache.return_value = True
        ex_map = OrderedDict([
            ('/etc/cinder/cinder.conf', ['cinder-api', 'cinder-volume',
                                         'cinder-scheduler', 'haproxy']),
            ('/etc/cinder/api-paste.ini', ['cinder-api']),
            ('/etc/haproxy/haproxy.cfg', ['haproxy']),
            ('/etc/memcached.conf', ['memcached']),
            ('/etc/apache2/sites-available/openstack_https_frontend.conf',
             ['apache2']),
        ])
        for cfg in ex_map.keys():
            self.assertEqual(cinder_utils.resource_map()[cfg]['services'],
                             ex_map[cfg])

    @patch('cinder_utils.service_enabled')
    @patch('os.path.exists')
    def test_creates_resource_map_no_api(self, path_exists,
                                         service_enabled):
        service_enabled.side_effect = self.svc_enabled
        self.test_config.set('enabled-services', 'scheduler,volume')
        path_exists.return_value = True
        self.os_release.return_value = 'havana'
        self.ceph_config_file.return_value = self.charm_ceph_conf
        self.relation_ids.return_value = []
        ex_map = OrderedDict([
            ('/etc/cinder/cinder.conf', ['cinder-volume',
                                         'cinder-scheduler']),
        ])
        for cfg in ex_map.keys():
            self.assertEqual(cinder_utils.resource_map()[cfg]['services'],
                             ex_map[cfg])

    @patch('cinder_utils.service_enabled')
    @patch('os.path.exists')
    def test_creates_resource_map_backup_backend(self, path_exists,
                                                 service_enabled):
        service_enabled.return_value = True
        path_exists.return_value = True
        self.os_release.return_value = 'havana'
        self.ceph_config_file.return_value = self.charm_ceph_conf
        self.relation_ids.side_effect = lambda x: {
            'storage-backend': [],
            'backup-backend': ['rid1'],
            'ceph': []}[x]
        self.assertTrue(
            'cinder-backup' in
            cinder_utils.resource_map()[self.cinder_conf]['services'])

    @patch('cinder_utils.service_enabled')
    @patch('os.path.exists')
    def test_creates_resource_map_no_backup(self, path_exists,
                                            service_enabled):
        service_enabled.return_value = True
        path_exists.return_value = True
        self.os_release.return_value = 'havana'
        self.ceph_config_file.return_value = self.charm_ceph_conf
        self.relation_ids.side_effect = lambda x: {
            'storage-backend': [],
            'backup-backend': [],
            'ceph': []}[x]
        self.assertFalse(
            'cinder-backup' in
            cinder_utils.resource_map()[self.cinder_conf]['services'])

    @patch('cinder_utils.service_enabled')
    @patch('os.path.exists')
    def test_creates_resource_map_no_ceph_conf(self, path_exists,
                                               service_enabled):
        service_enabled.return_value = True
        path_exists.return_value = True
        self.os_release.return_value = 'havana'
        self.ceph_config_file.return_value = self.charm_ceph_conf
        self.relation_ids.side_effect = lambda x: {
            'storage-backend': [],
            'backup-backend': [],
            'ceph': []}[x]
        self.assertFalse(self.charm_ceph_conf in
                         cinder_utils.resource_map().keys())

    @patch('cinder_utils.service_enabled')
    @patch('os.path.exists')
    def test_creates_resource_map_ceph_conf(self, path_exists,
                                            service_enabled):
        service_enabled.return_value = True
        path_exists.return_value = True
        self.os_release.return_value = 'havana'
        self.ceph_config_file.return_value = self.charm_ceph_conf
        self.relation_ids.side_effect = lambda x: {
            'storage-backend': [],
            'backup-backend': [],
            'ceph': ['rid1']}[x]
        self.assertTrue(self.charm_ceph_conf in
                        cinder_utils.resource_map().keys())
        self.mkdir.assert_has_calls(
            [call('/etc/ceph'),
             call('/var/lib/charm/cinder')]
        )
        self.install_alternative.assert_called_with(
            'ceph.conf',
            '/etc/ceph/ceph.conf',
            self.charm_ceph_conf)

    @patch('cinder_utils.service_enabled')
    @patch('os.path.exists')
    def test_creates_resource_map_old_apache(self, path_exists,
                                             service_enabled):
        service_enabled.return_value = True
        path_exists.side_effect = lambda x: x not in [self.apache24_conf_dir]
        self.os_release.return_value = 'havana'
        self.ceph_config_file.return_value = self.charm_ceph_conf
        self.relation_ids.side_effect = lambda x: {
            'storage-backend': [],
            'backup-backend': [],
            'ceph': []}[x]
        self.assertTrue(
            '/etc/apache2/sites-available/openstack_https_frontend' in
            cinder_utils.resource_map().keys())

    @patch('cinder_utils.service_enabled')
    @patch('os.path.exists')
    def test_creates_resource_map_apache24(self, path_exists, service_enabled):
        service_enabled.return_value = True
        path_exists.side_effect = lambda x: x in [self.apache24_conf_dir]
        self.os_release.return_value = 'havana'
        self.ceph_config_file.return_value = self.charm_ceph_conf
        self.relation_ids.side_effect = lambda x: {
            'storage-backend': [],
            'backup-backend': [],
            'ceph': []}[x]
        self.assertTrue(
            '/etc/apache2/sites-available/openstack_https_frontend.conf' in
            cinder_utils.resource_map().keys())

    @patch('cinder_utils.service_enabled')
    def test_filter_services_selective(self, service_enabled):
        service_enabled.side_effect = self.svc_enabled
        self.test_config.set('enabled-services', 'scheduler,volume')
        self.assertEqual(
            cinder_utils.filter_services(['cinder-api', 'cinder-volume',
                                          'haproxy']),
            ['cinder-volume']
        )

    @patch('cinder_utils.service_enabled')
    def test_filter_services_all(self, service_enabled):
        service_enabled.return_value = True
        self.test_config.set('enabled-services', 'scheduler,volume')
        self.assertEqual(
            cinder_utils.filter_services(['cinder-api', 'cinder-volume',
                                          'haproxy']),
            ['cinder-api', 'cinder-volume', 'haproxy']
        )

    @patch('cinder_utils.resource_map')
    def test_restart_map(self, resource_map):
        resource_map.return_value = OrderedDict([
            ('/etc/testfile1.conf', {
                'hook_contexts': ['dummyctxt1', 'dummyctxt2'],
                'services': ['svc1'],
            }),
            ('/etc/testfile2.conf', {
                'hook_contexts': ['dummyctxt1', 'dummyctxt3'],
                'services': [],
            }),
        ])
        ex_map = OrderedDict([
            ('/etc/testfile1.conf', ['svc1']),
        ])
        self.assertEqual(cinder_utils.restart_map(), ex_map)

    def test_clean_storage_unmount(self):
        'It unmounts block device when cleaning storage'
        self.is_lvm_physical_volume.return_value = False
        self.zap_disk.return_value = True
        self.mounts.return_value = MOUNTS
        cinder_utils.clean_storage('/dev/fakevbd')
        self.umount.called_with('/dev/fakevbd', True)

    def test_clean_storage_lvm_wipe(self):
        'It removes traces of LVM when cleaning storage'
        self.mounts.return_value = []
        self.is_lvm_physical_volume.return_value = True
        cinder_utils.clean_storage('/dev/fakevbd')
        self.remove_lvm_physical_volume.assert_called_with('/dev/fakevbd')
        self.deactivate_lvm_volume_group.assert_called_with('/dev/fakevbd')
        self.zap_disk.assert_called_with('/dev/fakevbd')

    def test_clean_storage_zap_disk(self):
        'It removes traces of LVM when cleaning storage'
        self.mounts.return_value = []
        self.is_lvm_physical_volume.return_value = False
        cinder_utils.clean_storage('/dev/fakevbd')
        self.zap_disk.assert_called_with('/dev/fakevbd')

    def test_parse_block_device(self):
        self.assertTrue(cinder_utils._parse_block_device(None),
                        (None, 0))
        self.assertTrue(cinder_utils._parse_block_device('fakevdc'),
                        ('/dev/fakevdc', 0))
        self.assertTrue(cinder_utils._parse_block_device('/dev/fakevdc'),
                        ('/dev/fakevdc', 0))
        self.assertTrue(cinder_utils._parse_block_device('/dev/fakevdc'),
                        ('/dev/fakevdc', 0))
        self.assertTrue(cinder_utils._parse_block_device('/mnt/loop0|10'),
                        ('/mnt/loop0', 10))
        self.assertTrue(cinder_utils._parse_block_device('/mnt/loop0'),
                        ('/mnt/loop0', cinder_utils.DEFAULT_LOOPBACK_SIZE))

    @patch('subprocess.check_output')
    def test_has_partition_table(self, _check):
        _check.return_value = FDISKDISPLAY.encode()
        block_device = '/dev/fakevbd'
        cinder_utils.has_partition_table(block_device)
        _check.assert_called_with(['fdisk', '-l', '/dev/fakevbd'], stderr=-2)

    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'ensure_lvm_volume_group_non_existent')
    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    @patch.object(cinder_utils, 'list_thin_logical_volume_pools')
    def test_configure_lvm_storage(self, list_thin_pools,
                                   extend_lvm, reduce_lvm, clean_storage,
                                   ensure_non_existent):
        devices = ['/dev/fakevbd', '/dev/fakevdc']
        self.is_device_mounted.return_value = False
        self.is_lvm_physical_volume.return_value = False
        self.is_block_device.return_value = True
        self.ensure_loopback_device.side_effect = lambda x, y: x
        cinder_utils.configure_lvm_storage(devices, 'test', True, True)
        clean_storage.assert_has_calls(
            [call('/dev/fakevbd'),
             call('/dev/fakevdc')]
        )
        self.create_lvm_physical_volume.assert_has_calls(
            [call('/dev/fakevbd'),
             call('/dev/fakevdc')]
        )
        self.create_lvm_volume_group.assert_called_with('test', '/dev/fakevbd')
        reduce_lvm.assert_called_with('test')
        extend_lvm.assert_called_with('test', '/dev/fakevdc')
        ensure_non_existent.assert_called_with('test')

    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'has_partition_table')
    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    @patch.object(cinder_utils, 'list_thin_logical_volume_pools')
    @patch.object(cinder_utils, 'extend_logical_volume_by_device')
    def test_configure_lvm_storage_unused_dev(self, extend_lv_by_dev,
                                              list_thin_pools,
                                              extend_lvm, reduce_lvm,
                                              clean_storage, has_part):
        devices = ['/dev/fakevbd', '/dev/fakevdc']
        self.is_device_mounted.return_value = False
        self.is_lvm_physical_volume.return_value = False
        self.is_block_device.return_value = True
        has_part.return_value = False
        self.ensure_loopback_device.side_effect = lambda x, y: x
        list_thin_pools.return_value = ['vg/thinpool']
        cinder_utils.configure_lvm_storage(devices, 'test', False, True)
        clean_storage.assert_has_calls(
            [call('/dev/fakevbd'),
             call('/dev/fakevdc')]
        )
        self.create_lvm_physical_volume.assert_has_calls(
            [call('/dev/fakevbd'),
             call('/dev/fakevdc')]
        )
        self.create_lvm_volume_group.assert_called_with('test', '/dev/fakevbd')
        reduce_lvm.assert_called_with('test')
        extend_lvm.assert_called_with('test', '/dev/fakevdc')
        extend_lv_by_dev.assert_called_once_with('vg/thinpool',
                                                 '/dev/fakevdc')

    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'has_partition_table')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    def test_configure_lvm_storage_used_dev(self, reduce_lvm, has_part):
        devices = ['/dev/fakevbd', '/dev/fakevdc']
        self.is_lvm_physical_volume.return_value = False
        has_part.return_value = True
        cinder_utils.configure_lvm_storage(devices, 'test', False, True)
        reduce_lvm.assert_called_with('test')

    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'ensure_lvm_volume_group_non_existent')
    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_loopback(self, extend_lvm, reduce_lvm,
                                            clean_storage,
                                            ensure_non_existent):
        devices = ['/mnt/loop0|10']
        self.ensure_loopback_device.return_value = '/dev/loop0'
        self.is_device_mounted.return_value = False
        self.is_lvm_physical_volume.return_value = False
        self.is_block_device.return_value = False
        cinder_utils.configure_lvm_storage(devices, 'test', True, True)
        clean_storage.assert_called_with('/dev/loop0')
        self.ensure_loopback_device.assert_called_with('/mnt/loop0', '10')
        self.create_lvm_physical_volume.assert_called_with('/dev/loop0')
        self.create_lvm_volume_group.assert_called_with('test', '/dev/loop0')
        reduce_lvm.assert_called_with('test')
        self.assertFalse(extend_lvm.called)
        ensure_non_existent.assert_called_with('test')

    @patch.object(cinder_utils, 'lvm_volume_group_exists')
    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    @patch.object(cinder_utils, 'list_thin_logical_volume_pools')
    def test_configure_lvm_storage_existing_vg(self, list_thin_pools,
                                               extend_lvm, reduce_lvm,
                                               clean_storage, lvm_exists):
        def pv_lookup(device):
            devices = {
                '/dev/fakevbd': True,
                '/dev/fakevdc': False
            }
            return devices[device]

        def vg_lookup(device):
            devices = {
                '/dev/fakevbd': 'test',
                '/dev/fakevdc': None
            }
            return devices[device]
        devices = ['/dev/fakevbd', '/dev/fakevdc']
        self.is_device_mounted.return_value = False
        self.is_block_device.return_value = True
        lvm_exists.return_value = False
        self.is_lvm_physical_volume.side_effect = pv_lookup
        self.list_lvm_volume_group.side_effect = vg_lookup
        self.ensure_loopback_device.side_effect = lambda x, y: x
        cinder_utils.configure_lvm_storage(devices, 'test', True, True)
        clean_storage.assert_has_calls(
            [call('/dev/fakevdc')]
        )
        self.create_lvm_physical_volume.assert_has_calls(
            [call('/dev/fakevdc')]
        )
        reduce_lvm.assert_called_with('test')
        extend_lvm.assert_called_with('test', '/dev/fakevdc')
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch.object(cinder_utils, 'lvm_volume_group_exists')
    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    @patch.object(cinder_utils, 'list_thin_logical_volume_pools')
    def test_configure_lvm_storage_different_vg(self, list_thin_pools,
                                                extend_lvm, reduce_lvm,
                                                clean_storage, lvm_exists):
        def pv_lookup(device):
            devices = {
                '/dev/fakevbd': True,
                '/dev/fakevdc': True
            }
            return devices[device]

        def vg_lookup(device):
            devices = {
                '/dev/fakevbd': 'test',
                '/dev/fakevdc': 'another'
            }
            return devices[device]
        devices = ['/dev/fakevbd', '/dev/fakevdc']
        self.is_device_mounted.return_value = False
        self.is_block_device.return_value = True
        self.is_lvm_physical_volume.side_effect = pv_lookup
        self.list_lvm_volume_group.side_effect = vg_lookup
        lvm_exists.return_value = False
        self.ensure_loopback_device.side_effect = lambda x, y: x
        cinder_utils.configure_lvm_storage(devices, 'test', True, True)
        clean_storage.assert_called_with('/dev/fakevdc')
        self.create_lvm_physical_volume.assert_called_with('/dev/fakevdc')
        reduce_lvm.assert_called_with('test')
        extend_lvm.assert_called_with('test', '/dev/fakevdc')
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'clean_storage')
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    @patch.object(cinder_utils, 'extend_lvm_volume_group')
    def test_configure_lvm_storage_different_vg_ignore(self, extend_lvm,
                                                       reduce_lvm,
                                                       clean_storage):
        def pv_lookup(device):
            devices = {
                '/dev/fakevbd': True,
                '/dev/fakevdc': True
            }
            return devices[device]

        def vg_lookup(device):
            devices = {
                '/dev/fakevbd': 'test',
                '/dev/fakevdc': 'another'
            }
            return devices[device]
        devices = ['/dev/fakevbd', '/dev/fakevdc']
        self.is_lvm_physical_volume.side_effect = pv_lookup
        self.list_lvm_volume_group.side_effect = vg_lookup
        self.ensure_loopback_device.side_effect = lambda x, y: x
        cinder_utils.configure_lvm_storage(devices, 'test', False, False)
        self.assertFalse(clean_storage.called)
        self.assertFalse(self.create_lvm_physical_volume.called)
        self.assertFalse(reduce_lvm.called)
        self.assertFalse(extend_lvm.called)
        self.assertFalse(self.create_lvm_volume_group.called)

    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    def test_configure_lvm_storage_unforced_remove_default(self, reduce_lvm):
        """It doesn't force remove missing by default."""
        devices = ['/dev/fakevbd']
        cinder_utils.configure_lvm_storage(devices, 'test', False, True)
        reduce_lvm.assert_called_with('test')

    @patch('cinder_utils.log_lvm_info', Mock())
    @patch.object(cinder_utils, 'reduce_lvm_volume_group_missing')
    def test_configure_lvm_storage_force_removemissing(self, reduce_lvm):
        """It forces remove missing when asked to."""
        devices = ['/dev/fakevbd']
        cinder_utils.configure_lvm_storage(
            devices, 'test', False, True, remove_missing_force=True)
        reduce_lvm.assert_called_with('test', extra_args=['--force'])

    @patch('subprocess.check_call')
    def test_reduce_lvm_volume_group_missing(self, _call):
        cinder_utils.reduce_lvm_volume_group_missing('test')
        _call.assert_called_with(['vgreduce', '--removemissing', 'test'])

    @patch('subprocess.check_call')
    def test_reduce_lvm_volume_group_missing_extra_args(self, _call):
        cinder_utils.reduce_lvm_volume_group_missing(
            'test', extra_args=['--arg'])
        expected_call_args = ['vgreduce', '--removemissing', '--arg', 'test']
        _call.assert_called_with(expected_call_args)

    @patch('subprocess.check_call')
    def test_extend_lvm_volume_group(self, _call):
        cinder_utils.extend_lvm_volume_group('test', '/dev/sdb')
        _call.assert_called_with(['vgextend', 'test', '/dev/sdb'])

    @patch.object(cinder_utils, 'relation_get')
    @patch.object(cinder_utils, 'relation_ids')
    @patch.object(cinder_utils, 'related_units')
    def test_is_db_initisalised_false(self, mock_related_units,
                                      mock_relation_ids,
                                      mock_relation_get):
        settings = {'0': {}, '1': {}}

        def mock_rel_get(unit=None, rid=None, attribute=None):
            if not unit:
                unit = '0'

            if attribute:
                return settings[unit].get(attribute)

            return settings[unit]

        cinder_utils.local_unit.return_value = '0'
        mock_relation_get.side_effect = mock_rel_get
        mock_related_units.return_value = ['1']
        mock_relation_ids.return_value = ['cluster:1']
        self.assertFalse(cinder_utils.is_db_initialised())

    @patch.object(cinder_utils, 'relation_get')
    @patch.object(cinder_utils, 'relation_ids')
    @patch.object(cinder_utils, 'related_units')
    def test_is_db_initisalised_true(self, mock_related_units,
                                     mock_relation_ids,
                                     mock_relation_get):
        settings = {'0': {cinder_utils.CINDER_DB_INIT_RKEY: '1234'},
                    '1': {cinder_utils.CINDER_DB_INIT_ECHO_RKEY: '1234'}}

        def mock_rel_get(unit=None, rid=None, attribute=None):
            if not unit:
                unit = '0'

            if attribute:
                return settings[unit].get(attribute)

            return settings[unit]

        cinder_utils.local_unit.return_value = '0'
        mock_relation_get.side_effect = mock_rel_get
        mock_related_units.return_value = ['1']
        mock_relation_ids.return_value = ['cluster:1']
        self.assertTrue(cinder_utils.is_db_initialised())

    @patch.object(cinder_utils, 'is_db_initialised')
    @patch.object(cinder_utils, 'enabled_services')
    @patch.object(cinder_utils, 'local_unit', lambda *args: 'unit/0')
    @patch.object(cinder_utils, 'uuid')
    def test_migrate_database(self, mock_uuid, mock_enabled_services,
                              mock_is_db_initialised):
        'It migrates database with cinder-manage'
        mock_is_db_initialised.return_value = False
        uuid = 'a-great-uuid'
        mock_uuid.uuid4.return_value = uuid
        mock_enabled_services.return_value = ['svc1']
        rid = 'cluster:0'
        self.relation_ids.return_value = [rid]
        args = {'cinder-db-initialised': "unit/0-%s" % uuid}
        with patch('subprocess.check_output') as check_output:
            cinder_utils.migrate_database()
            check_output.assert_called_once_with(
                ['cinder-manage', 'db', 'sync'], universal_newlines=True
            )
            self.relation_set.assert_called_with(relation_id=rid, **args)
            self.service_restart.assert_called_with('svc1')

    @patch.object(cinder_utils, 'is_db_initialised')
    def test_migrate_database_already_initisalised(self,
                                                   mock_is_db_initialised):
        mock_is_db_initialised.return_value = True
        with patch('subprocess.check_output') as check_call:
            cinder_utils.migrate_database()
            self.assertFalse(check_call.called)
            self.assertFalse(self.service_restart.called)

    @patch.object(cinder_utils, 'is_db_initialised')
    @patch.object(cinder_utils, 'enabled_services')
    @patch.object(cinder_utils, 'local_unit', lambda *args: 'unit/0')
    @patch.object(cinder_utils, 'uuid')
    def test_migrate_database_need_online_migration(
            self, mock_uuid, mock_enabled_services, mock_is_db_initialised):
        'It runs online migrations when required'
        mock_is_db_initialised.return_value = True
        uuid = 'a-great-uuid'
        mock_uuid.uuid4.return_value = uuid
        mock_enabled_services.return_value = ['svc1']
        rid = 'cluster:0'
        self.relation_ids.return_value = [rid]
        with patch('subprocess.check_output') as check_output, \
             patch('subprocess.check_call') as check_call, \
             patch('subprocess.run') as run:
            check_output.side_effect = subprocess.CalledProcessError(
                1, 'cinder db-manage sync',
                ("Please run `cinder-manage db online_data_migrations`. "
                 "There are still untyped volumes unmigrated."))
            run_result = MagicMock()
            run_result.returncode = 0
            run.return_value = run_result
            cinder_utils.migrate_database(upgrade=True)
            check_output.assert_called_with(['cinder-manage', 'db', 'sync'],
                                            universal_newlines=True)
            check_call.assert_has_calls([
                call(['cinder-manage', 'db', 'sync'])
            ])
            run.assert_has_calls([
                call(['cinder-manage', 'db', 'online_data_migrations',
                      '--max_count', cinder_utils.MAX_OBJECTS_COUNT],
                     check=False),
            ])

            run_result.returncode = 2
            self.assertRaises(subprocess.CalledProcessError,
                              cinder_utils.migrate_database,
                              upgrade=True)

    @patch.object(cinder_utils, 'resource_map')
    def test_register_configs(self, resource_map):
        resource_map.return_value = OrderedDict([
            ('/etc/testfile1.conf', {
                'contexts': ['dummyctxt1', 'dummyctxt2'],
                'services': ['svc1'],
            }),
            ('/etc/testfile2.conf', {
                'contexts': ['dummyctxt1', 'dummyctxt3'],
                'services': [],
            }),
        ])
        configs = cinder_utils.register_configs()
        calls = [
            call('/etc/testfile1.conf', ['dummyctxt1', 'dummyctxt2']),
            call('/etc/testfile2.conf', ['dummyctxt1', 'dummyctxt3']),
        ]
        configs.register.assert_has_calls(calls)

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

    @patch.object(cinder_utils, 'get_subordinate_release_packages')
    @patch.object(cinder_utils, 'service_enabled')
    @patch.object(cinder_utils, 'register_configs')
    @patch.object(cinder_utils, 'services')
    @patch.object(cinder_utils, 'migrate_database')
    @patch.object(cinder_utils, 'determine_packages')
    def test_openstack_upgrade_leader(self, pkgs, migrate, services,
                                      mock_register_configs, service_enabled,
                                      mock_get_subordinate_release_packages):
        pkgs.return_value = ['mypackage']
        self.os_release.return_value = 'havana'
        self.config.side_effect = None
        self.config.return_value = 'cloud:precise-havana'
        services.return_value = ['cinder-api', 'cinder-volume']
        self.is_elected_leader.return_value = True
        self.get_os_codename_install_source.return_value = 'havana'
        configs = mock_register_configs.return_value
        service_enabled.return_value = True
        cinder_utils.do_openstack_upgrade(configs)
        self.assertTrue(mock_register_configs.called)
        self.assertTrue(configs.write_all.called)
        self.apt_upgrade.assert_called_with(options=DPKG_OPTIONS,
                                            fatal=True, dist=True)
        self.apt_install.assert_called_with(['mypackage'], fatal=True)
        configs.set_release.assert_called_with(openstack_release='havana')
        self.assertTrue(migrate.called)

    @patch.object(cinder_utils, 'get_subordinate_release_packages')
    @patch.object(cinder_utils, 'service_enabled')
    @patch.object(cinder_utils, 'register_configs')
    @patch.object(cinder_utils, 'services')
    @patch.object(cinder_utils, 'migrate_database')
    @patch.object(cinder_utils, 'determine_packages')
    def test_openstack_upgrade_not_leader(
            self,
            pkgs,
            migrate,
            services,
            mock_register_configs,
            service_enabled,
            mock_get_subordinate_release_packages):
        pkgs.return_value = ['mypackage']
        self.os_release.return_value = 'havana'
        self.config.side_effect = None
        self.config.return_value = 'cloud:precise-havana'
        services.return_value = ['cinder-api', 'cinder-volume']
        self.is_elected_leader.return_value = False
        self.get_os_codename_install_source.return_value = 'havana'
        configs = mock_register_configs.return_value
        service_enabled.return_value = True
        cinder_utils.do_openstack_upgrade(configs)
        self.assertTrue(mock_register_configs.called)
        self.assertTrue(configs.write_all.called)
        self.apt_upgrade.assert_called_with(options=DPKG_OPTIONS,
                                            fatal=True, dist=True)
        self.apt_install.assert_called_with(['mypackage'], fatal=True)
        configs.set_release.assert_called_with(openstack_release='havana')
        self.assertFalse(migrate.called)

    @patch.object(cinder_utils, 'get_subordinate_release_packages')
    @patch.object(cinder_utils, 'service_enabled')
    @patch.object(cinder_utils, 'register_configs')
    @patch.object(cinder_utils, 'services')
    @patch.object(cinder_utils, 'migrate_database')
    @patch.object(cinder_utils, 'determine_packages')
    def test_openstack_upgrade_rocky(self, pkgs, migrate, services,
                                     mock_register_configs,
                                     service_enabled,
                                     mock_get_subordinate_release_packages):
        pkgs.return_value = ['mypackage']
        self.os_release.return_value = 'rocky'
        self.config.side_effect = None
        self.config.return_value = 'cloud:bionic-rocky'
        services.return_value = ['cinder-api', 'cinder-volume']
        self.is_elected_leader.return_value = True
        self.get_os_codename_install_source.return_value = 'rocky'
        configs = mock_register_configs.return_value
        service_enabled.return_value = True
        self.filter_missing_packages.return_value = [
            'python-cinder',
        ]
        cinder_utils.do_openstack_upgrade(configs)
        self.assertTrue(mock_register_configs.called)
        self.assertTrue(configs.write_all.called)
        self.apt_upgrade.assert_called_with(options=DPKG_OPTIONS,
                                            fatal=True, dist=True)
        self.apt_install.assert_called_with(['mypackage'], fatal=True)
        self.apt_purge.assert_called_with(
            ['python-cinder'],
            fatal=True)
        self.apt_autoremove.assert_called_with(purge=True, fatal=True)
        configs.set_release.assert_called_with(openstack_release='rocky')
        self.assertTrue(migrate.called)

    @patch.object(cinder_utils, 'local_unit', lambda *args: 'unit/0')
    def test_check_local_db_actions_complete_by_self(self):
        self.relation_get.return_value = {}
        cinder_utils.check_local_db_actions_complete()
        self.assertFalse(self.relation_set.called)

        self.relation_get.return_value = {'cinder-db-initialised':
                                          'unit/0-1234'}
        cinder_utils.check_local_db_actions_complete()
        self.assertFalse(self.relation_set.called)

    @patch.object(cinder_utils, 'is_db_initialised')
    @patch.object(cinder_utils, 'enabled_services')
    @patch.object(cinder_utils, 'local_unit', lambda *args: 'unit/0')
    def test_check_local_db_actions_complete(self, enabled_services,
                                             mock_is_db_initialised):
        mock_is_db_initialised.return_value = True
        enabled_services.return_value = ['svc1']
        r_settings = {}

        def mock_relation_get(unit=None, rid=None, attribute=None):
            if attribute:
                return r_settings.get(attribute)
            else:
                return r_settings

        # no clear of cinder_utils.CINDER_DB_INIT_RKEY
        self.is_elected_leader.return_value = True
        self.relation_get.side_effect = mock_relation_get
        cinder_utils.check_local_db_actions_complete()
        self.assertFalse(self.relation_set.called)
        r_settings = {'cinder-db-initialised': 'unit/1-1234'}
        cinder_utils.check_local_db_actions_complete()
        calls = [call(**{'cinder-db-initialised-echo': 'unit/1-1234'})]
        self.relation_set.assert_has_calls(calls)
        self.service_restart.assert_called_with('svc1')

    @patch.object(cinder_utils, 'is_db_initialised')
    @patch.object(cinder_utils, 'enabled_services')
    @patch.object(cinder_utils, 'local_unit', lambda *args: 'unit/0')
    def test_check_local_db_actions_complete_not_leader(
            self, enabled_services, mock_is_db_initialised):
        mock_is_db_initialised.return_value = True
        enabled_services.return_value = ['svc1']
        r_settings = {}

        def mock_relation_get(unit=None, rid=None, attribute=None):
            if attribute:
                return r_settings.get(attribute)
            else:
                return r_settings

        # ensure clear of cinder_utils.CINDER_DB_INIT_RKEY
        self.is_elected_leader.return_value = False
        self.relation_get.side_effect = mock_relation_get
        cinder_utils.check_local_db_actions_complete()
        self.assertFalse(self.relation_set.called)
        r_settings = {'cinder-db-initialised': 'unit/1-1234'}
        cinder_utils.check_local_db_actions_complete()
        calls = [call(
            **{'cinder-db-initialised-echo': 'unit/1-1234',
               cinder_utils.CINDER_DB_INIT_RKEY: None})]
        self.relation_set.assert_has_calls(calls)
        self.service_restart.assert_called_with('svc1')

    @patch('subprocess.check_output')
    def test_log_lvm_info(self, _check):
        output = b"some output"
        _check.return_value = output
        cinder_utils.log_lvm_info()
        _check.assert_called_with(['pvscan'])
        self.juju_log.assert_called_with("pvscan: {}".format(output.decode()))

    @patch.object(cinder_utils, 'lvm_volume_group_exists')
    @patch.object(cinder_utils, 'remove_lvm_volume_group')
    def test_ensure_non_existent_removes_if_present(self,
                                                    remove_lvm_volume_group,
                                                    volume_group_exists):
        volume_group = "test"
        volume_group_exists.return_value = True
        cinder_utils.ensure_lvm_volume_group_non_existent(volume_group)
        remove_lvm_volume_group.assert_called_with(volume_group)

    @patch.object(cinder_utils, 'lvm_volume_group_exists')
    @patch.object(cinder_utils, 'remove_lvm_volume_group')
    def test_ensure_non_existent_not_present(self, remove_lvm_volume_group,
                                             volume_group_exists):
        volume_group = "test"
        volume_group_exists.return_value = False
        cinder_utils.ensure_lvm_volume_group_non_existent(volume_group)
        self.assertFalse(remove_lvm_volume_group.called)

    @patch('subprocess.check_call')
    def test_lvm_volume_group_exists_finds_volume_group(self, _check):
        volume_group = "test"
        _check.return_value = True
        result = cinder_utils.lvm_volume_group_exists(volume_group)
        self.assertTrue(result)
        _check.assert_called_with(['vgdisplay', volume_group])

    @patch('subprocess.check_call')
    def test_lvm_volume_group_exists_finds_no_volume_group(self, _check):
        volume_group = "test"

        def raise_error(x):
            raise subprocess.CalledProcessError(1, x)
        _check.side_effect = raise_error
        result = cinder_utils.lvm_volume_group_exists(volume_group)
        self.assertFalse(result)
        _check.assert_called_with(['vgdisplay', volume_group])

    @patch('subprocess.check_call')
    def test_remove_lvm_volume_group(self, _check):
        volume_group = "test"
        cinder_utils.remove_lvm_volume_group(volume_group)
        _check.assert_called_with(['vgremove', '--force', volume_group])

    def test_required_interfaces_api(self):
        '''identity-service interface required for api service'''
        expected = {
            'database': ('shared-db',),
            'messaging': ('amqp',),
            'identity': ('identity-service',),
        }
        self.assertEqual(cinder_utils.required_interfaces(), expected)

    def test_required_interfaces_no_api(self):
        '''
        identity-service interface not required for volume
        or scheduler service
        '''
        self.test_config.set('enabled-services', 'volume,scheduler')
        expected = {
            'database': ('shared-db',),
            'messaging': ('amqp',),
        }
        self.assertEqual(cinder_utils.required_interfaces(), expected)

    def test_assess_status(self):
        with patch.object(cinder_utils, 'assess_status_func') as asf:
            callee = MagicMock()
            asf.return_value = callee
            cinder_utils.assess_status('test-config')
            asf.assert_called_once_with('test-config')
            callee.assert_called_once_with()
            self.os_application_version_set.assert_called_with(
                'cinder-common'
            )

    @patch.object(cinder_utils, 'get_managed_services_and_ports')
    @patch.object(cinder_utils, 'get_optional_interfaces')
    @patch.object(cinder_utils, 'check_optional_relations')
    @patch.object(cinder_utils, 'required_interfaces')
    @patch.object(cinder_utils, 'services')
    @patch.object(cinder_utils, 'make_assess_status_func')
    def test_assess_status_func(self,
                                make_assess_status_func,
                                services,
                                required_interfaces,
                                check_optional_relations,
                                get_optional_interfaces,
                                get_managed_services_and_ports):
        services.return_value = ['s1']
        get_managed_services_and_ports.return_value = (['s1'], [])
        required_interfaces.return_value = {'int': ['test 1']}
        get_optional_interfaces.return_value = {'opt': ['test 2']}
        cinder_utils.assess_status_func('test-config')
        # ports=None whilst port checks are disabled.
        make_assess_status_func.assert_called_once_with(
            'test-config',
            {'int': ['test 1'], 'opt': ['test 2']},
            charm_func=check_optional_relations,
            services=['s1'], ports=None)

    def test_pause_unit_helper(self):
        with patch.object(cinder_utils, '_pause_resume_helper') as prh:
            cinder_utils.pause_unit_helper('random-config')
            prh.assert_called_once_with(cinder_utils.pause_unit,
                                        'random-config')
        with patch.object(cinder_utils, '_pause_resume_helper') as prh:
            cinder_utils.resume_unit_helper('random-config')
            prh.assert_called_once_with(cinder_utils.resume_unit,
                                        'random-config')

    @patch.object(cinder_utils, 'get_managed_services_and_ports')
    @patch.object(cinder_utils, 'services')
    def test_pause_resume_helper(self, services,
                                 get_managed_services_and_ports):
        f = MagicMock()
        services.return_value = ['s1']
        get_managed_services_and_ports.return_value = (['s1'], [])
        with patch.object(cinder_utils, 'assess_status_func') as asf:
            asf.return_value = 'assessor'
            cinder_utils._pause_resume_helper(f, 'some-config')
            asf.assert_called_once_with('some-config')
            # ports=None whilst port checks are disabled.
            f.assert_called_once_with('assessor', services=['s1'], ports=None)
