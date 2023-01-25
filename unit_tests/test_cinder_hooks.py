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
import json

from six.moves import reload_module

from unittest.mock import (
    patch,
    call
)

from test_utils import (
    CharmTestCase,
    RESTART_MAP,
)

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    with patch('cinder_utils.register_configs') as register_configs:
        with patch('cinder_utils.restart_map') as restart_map:
            restart_map.return_value = RESTART_MAP
            import cinder_hooks as hooks
            reload_module(hooks)

hooks.hooks._config_save = False
import cinder_utils as utils

TO_PATCH = [
    'check_call',
    'send_request_if_needed',
    'is_request_complete',
    # cinder_utils
    'configure_lvm_storage',
    'determine_packages',
    'do_openstack_upgrade',
    'ensure_ceph_keyring',
    'is_clustered',
    'juju_log',
    'log',
    'lsb_release',
    'migrate_database',
    'register_configs',
    'restart_map',
    'service_enabled',
    'CONFIGS',
    'CLUSTER_RES',
    'CEPH_CONF',
    'ceph_config_file',
    'update_nrpe_config',
    'remove_old_packages',
    # charmhelpers.core.hookenv
    'config',
    'local_unit',
    'relation_get',
    'relation_ids',
    'relation_set',
    'related_units',
    'service_name',
    'open_port',
    # charmhelpers.core.host
    'apt_install',
    'apt_update',
    'filter_installed_packages',
    'service_reload',
    'service_restart',
    'service_start',
    # charmhelpers.contrib.openstack.openstack_utils
    'configure_installation_source',
    'openstack_upgrade_available',
    'os_release',
    'run_in_apache',
    # charmhelpers.contrib.openstack.policyd
    'maybe_do_policyd_overrides',
    'maybe_do_policyd_overrides_on_config_changed',
    # charmhelpers.contrib.openstack.openstack.ha.utils
    'generate_ha_relation_data',
    # charmhelpers.contrib.hahelpers.cluster_utils
    'is_elected_leader',
    'execd_preinstall',
    'sync_db_with_multi_ipv6_addresses',
    'delete_keyring',
    'remove_alternative',
    'get_relation_ip',
    'services',
    'is_db_maintenance_mode',
]


class TestInstallHook(CharmTestCase):

    def setUp(self):
        super(TestInstallHook, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get

    def test_install_precise_distro(self):
        'It redirects to cloud archive if setup to install precise+distro'
        self.lsb_release.return_value = {'DISTRIB_CODENAME': 'precise'}
        self.test_config.set('openstack-origin', 'distro')
        hooks.hooks.execute(['hooks/install.real'])
        ca = 'cloud:precise-folsom'
        self.configure_installation_source.assert_called_with(ca)

    def test_correct_install_packages(self):
        'It installs the correct packages based on what is determined'
        self.determine_packages.return_value = ['foo', 'bar', 'baz']
        hooks.hooks.execute(['hooks/install.real'])
        self.apt_install.assert_called_with(['foo', 'bar', 'baz'], fatal=True)


class TestChangedHooks(CharmTestCase):

    _relations = {
        'identity-service': ['identity-service:1'],
    }

    def svc_enabled(self, svc):
        enabled = self.test_config.get('enabled-services')

        if enabled == 'all':
            return True

        return svc in enabled

    def setUp(self):
        super(TestChangedHooks, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get

    @patch.object(hooks, 'scrub_old_style_ceph')
    @patch.object(hooks, 'amqp_joined')
    def test_upgrade_charm_no_amqp(self, _joined, _scrub_old_style_ceph):
        self.remove_old_packages.return_value = False
        self.relation_ids.return_value = []
        hooks.hooks.execute(['hooks/upgrade-charm'])
        _joined.assert_not_called()
        _scrub_old_style_ceph.assert_called_once_with()

    @patch.object(hooks, 'scrub_old_style_ceph')
    @patch.object(hooks, 'amqp_joined')
    def test_upgrade_charm_purge(self, _joined, _scrub_old_style_ceph):
        self.remove_old_packages.return_value = True
        self.services.return_value = ['cinder-api']
        self.relation_ids.return_value = []
        hooks.hooks.execute(['hooks/upgrade-charm'])
        _joined.assert_not_called()
        _scrub_old_style_ceph.assert_called_once_with()
        self.service_restart.assert_called_once_with('cinder-api')

    @patch.object(hooks, 'scrub_old_style_ceph')
    @patch.object(hooks, 'amqp_joined')
    def test_upgrade_charm_with_amqp(self, _joined, _scrub_old_style_ceph):
        self.relation_ids.return_value = ['amqp:1']
        hooks.hooks.execute(['hooks/upgrade-charm'])
        _joined.assert_called_with(relation_id='amqp:1')
        _scrub_old_style_ceph.assert_called_once_with()

    @patch.object(hooks, 'identity_joined')
    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed(self, config_val_changed,
                            conf_https, identity_joined):
        'It writes out all config'
        self.openstack_upgrade_available.return_value = False
        self.relation_ids.side_effect = (
            lambda rname: self._relations.get(rname, [])
        )
        hooks.hooks.execute(['hooks/config-changed'])
        self.assertTrue(self.CONFIGS.write_all.called)
        self.assertTrue(conf_https.called)
        self.configure_lvm_storage.assert_called_with(['sdb'],
                                                      'cinder-volumes',
                                                      False, False, False)
        self.open_port.assert_called_with(8776)
        identity_joined.assert_called_once_with(rid='identity-service:1')

    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_block_devices(self, config_val_changed,
                                          conf_https):
        'It writes out all config'
        self.openstack_upgrade_available.return_value = False
        self.test_config.set('block-device', 'sdb /dev/sdc sde')
        self.test_config.set('volume-group', 'cinder-new')
        self.test_config.set('overwrite', 'True')
        self.test_config.set('remove-missing', True)
        hooks.hooks.execute(['hooks/config-changed'])
        self.assertTrue(self.CONFIGS.write_all.called)
        self.assertTrue(conf_https.called)
        self.configure_lvm_storage.assert_called_with(
            ['sdb', '/dev/sdc', 'sde'],
            'cinder-new',
            True, True, False)

    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_uses_remove_missing_force(self,
                                                      config_val_changed,
                                                      conf_https):
        'It uses the remove-missing-force config option'
        self.openstack_upgrade_available.return_value = False
        self.test_config.set('block-device', 'sdb')
        self.test_config.set('remove-missing-force', True)
        hooks.hooks.execute(['hooks/config-changed'])
        self.configure_lvm_storage.assert_called_with(
            ['sdb'],
            'cinder-volumes',
            False, False, True)

    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_upgrade_available(self, config_val_changed,
                                              conf_https):
        'It writes out all config with an available OS upgrade'
        self.openstack_upgrade_available.return_value = True
        hooks.hooks.execute(['hooks/config-changed'])
        self.do_openstack_upgrade.assert_called_with(configs=self.CONFIGS)

    @patch('charmhelpers.core.host.service')
    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_overwrite_changed(self, config_val_changed,
                                              conf_https,
                                              _services):
        'It uses the overwrite config option'
        self.openstack_upgrade_available.return_value = False
        config_val_changed.return_value = True
        hooks.hooks.execute(['hooks/config-changed'])
        self.assertTrue(self.CONFIGS.write_all.called)
        self.assertTrue(conf_https.called)
        self.configure_lvm_storage.assert_called_with(['sdb'],
                                                      'cinder-volumes',
                                                      False, False, False)
        self.service_restart.assert_called_with('cinder-volume')

    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_with_openstack_upgrade_action(
            self,
            config_value_changed):
        self.openstack_upgrade_available.return_value = True
        self.test_config.set('action-managed-upgrade', True)

        hooks.hooks.execute(['hooks/config-changed'])

        self.assertFalse(self.do_openstack_upgrade.called)

    def test_db_changed(self):
        'It writes out cinder.conf on db changed'
        self.is_db_maintenance_mode.return_value = False
        self.relation_get.return_value = 'cinder/0 cinder/1'
        self.local_unit.return_value = 'cinder/0'
        self.CONFIGS.complete_contexts.return_value = ['shared-db']
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')
        self.assertTrue(self.migrate_database.called)

    def test_db_changed_relation_incomplete(self):
        'It does not write out cinder.conf with incomplete shared-db rel'
        self.is_db_maintenance_mode.return_value = False
        self.relation_get.return_value = 'cinder/0 cinder/1'
        self.local_unit.return_value = 'cinder/0'
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.assertFalse(self.CONFIGS.write.called)
        self.assertFalse(self.migrate_database.called)

    def test_db_changed_relation_db_no_acl(self):
        'It does not migration when acl entry not present'
        self.is_db_maintenance_mode.return_value = False
        self.relation_get.return_value = 'cinder/1 cinder/2'
        self.local_unit.return_value = 'cinder/0'
        self.CONFIGS.complete_contexts.return_value = ['shared-db']
        self.is_elected_leader.return_value = True
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.assertFalse(self.migrate_database.called)

    def test_db_changed_relation_db_missing_acls(self):
        'No database migration is attempted when ACL list is not present'
        self.is_db_maintenance_mode.return_value = False
        self.relation_get.return_value = None
        self.local_unit.return_value = 'cinder/0'
        self.CONFIGS.complete_contexts.return_value = ['shared-db']
        self.is_elected_leader.return_value = True
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.assertFalse(self.migrate_database.called)

    def test_db_changed_not_leader(self):
        'It does not migrate database when not leader'
        self.is_db_maintenance_mode.return_value = False
        self.relation_get.return_value = 'cinder/0 cinder/1'
        self.local_unit.return_value = 'cinder/0'
        self.is_elected_leader.return_value = False
        self.CONFIGS.complete_contexts.return_value = ['shared-db']
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')
        self.assertFalse(self.migrate_database.called)

    def test_amqp_changed(self):
        'It writes out cinder.conf on amqp changed with complete relation'
        self.CONFIGS.complete_contexts.return_value = ['amqp']
        hooks.hooks.execute(['hooks/amqp-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')

    def test_amqp_changed_incomplete(self):
        'It does not write out cinder.conf with incomplete relation'
        self.CONFIGS.complete_contexts.return_value = ['']
        hooks.hooks.execute(['hooks/amqp-relation-changed'])
        self.assertFalse(self.CONFIGS.write.called)

    def test_identity_joined_partial_cluster(self):
        self.is_clustered.return_value = False
        self.test_config.set('vip', '10.0.0.10')
        hooks.identity_joined()
        self.assertFalse(self.relation_set.called)

    @patch.object(hooks, 'configure_https')
    def test_identity_changed(self, conf_https):
        'It writes out api-paste.ini on identity-service changed'
        self.CONFIGS.complete_contexts.return_value = ['identity-service']
        hooks.hooks.execute(['hooks/identity-service-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/api-paste.ini')
        self.assertTrue(conf_https.called)

    def test_identity_changed_incomplete(self):
        'It does not write api-paste.ini with incomplete identity-service'
        hooks.hooks.execute(['hooks/identity-service-relation-changed'])
        self.assertFalse(self.CONFIGS.write.called)

    @patch.object(hooks, 'service_enabled')
    def test_identity_credentials_joined_without_api(self, service_enabled):
        'It requests keystone credentials without API service'
        self.test_config.set('enabled-services', 'volume')
        service_enabled.side_effect = self.svc_enabled
        hooks.hooks.execute(['hooks/identity-credentials-relation-joined'])
        expected = {'relation_id': None,
                    'username': 'cinder',
                    'requested_roles': 'Admin'}
        self.relation_set.assert_called_with(**expected)

    @patch.object(hooks, 'service_enabled')
    def test_identity_credentials_joined_with_api(self, service_enabled):
        'It requests keystone credentials with API service'
        self.test_config.set('enabled-services', 'all')
        service_enabled.side_effect = self.svc_enabled
        hooks.hooks.execute(['hooks/identity-credentials-relation-joined'])
        self.relation_set.assert_not_called()

    @patch.object(hooks, 'service_enabled')
    def test_identity_credentials_changed(self, service_enabled):
        'It writes out cinder.conf on identity-credentials changed'
        self.CONFIGS.complete_contexts.return_value = ['identity-credentials']
        self.test_config.set('enabled-services', 'volume')
        service_enabled.side_effect = self.svc_enabled
        hooks.hooks.execute(['hooks/identity-credentials-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')

    @patch.object(hooks, 'service_enabled')
    def test_identity_credentials_changed_incomplete(self, service_enabled):
        'It does not write cinder.conf with incomplete identity-service'
        self.test_config.set('enabled-services', 'volume')
        service_enabled.side_effect = self.svc_enabled
        hooks.hooks.execute(['hooks/identity-credentials-relation-changed'])
        self.assertFalse(self.CONFIGS.write.called)

    @patch.object(hooks, 'identity_joined')
    def test_configure_https_enable(self, identity_joined):
        'It enables https from hooks when we have https data'
        self.CONFIGS.complete_contexts.return_value = ['https']
        self.relation_ids.return_value = ['identity-service:0']
        hooks.configure_https()
        self.check_call.assert_called_with(['a2ensite',
                                            'openstack_https_frontend'])
        self.service_reload.assert_called_with('apache2',
                                               restart_on_failure=True)
        identity_joined.assert_called_with(rid='identity-service:0')

    @patch.object(hooks, 'identity_joined')
    def test_configure_https_disable(self, identity_joined):
        'It enables https from hooks when we have https data'
        self.CONFIGS.complete_contexts.return_value = []
        self.relation_ids.return_value = ['identity-service:0']
        hooks.configure_https()
        self.check_call.assert_called_with(['a2dissite',
                                            'openstack_https_frontend'])
        self.service_reload.assert_called_with('apache2',
                                               restart_on_failure=True)
        identity_joined.assert_called_with(rid='identity-service:0')

    def test_image_service_changed(self):
        'Ensure all configuration files written if image service changes'
        hooks.hooks.execute(['hooks/image-service-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')

    def test_relation_broken(self):
        'Ensure all configuration files written if image service changes'
        hooks.hooks.execute(['hooks/image-service-relation-broken'])
        self.assertTrue(self.CONFIGS.write_all.called)

    def test_storage_backend_changed(self):
        hooks.hooks.execute(['hooks/storage-backend-relation-changed'])
        self.CONFIGS.write.assert_called_with(utils.CINDER_CONF)

    def test_storage_backend_broken(self):
        hooks.hooks.execute(['hooks/storage-backend-relation-broken'])
        self.CONFIGS.write.assert_called_with(utils.CINDER_CONF)


class TestJoinedHooks(CharmTestCase):

    def setUp(self):
        super(TestJoinedHooks, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get

    def test_db_joined(self):
        'It properly requests access to a shared-db service'
        self.get_relation_ip.return_value = '10.0.0.1'
        hooks.hooks.execute(['hooks/shared-db-relation-joined'])
        expected = {'username': 'cinder',
                    'hostname': '10.0.0.1', 'database': 'cinder'}
        self.relation_set.assert_called_with(**expected)

    def test_amqp_joined(self):
        'It properly requests access to an amqp service'
        hooks.hooks.execute(['hooks/amqp-relation-joined'])
        self.relation_set.assert_called_with(username='cinder',
                                             vhost='openstack',
                                             relation_id=None)

    def test_amqp_joined_passes_relation_id(self):
        '''Ensures relation_id correct passed to relation_set for out of
            hook execution
        '''
        hooks.amqp_joined(relation_id='amqp:1')
        self.relation_set.assert_called_with(username='cinder',
                                             vhost='openstack',
                                             relation_id='amqp:1')

    @patch.object(hooks, 'canonical_url')
    def test_identity_service_joined(self, _canonical_url):
        'It properly requests unclustered endpoint via identity-service'
        self.os_release.return_value = 'havana'
        self.config.side_effect = self.test_config.get
        _canonical_url.return_value = 'http://cindernode1'
        hooks.hooks.execute(['hooks/identity-service-relation-joined'])
        expected = {
            'region': None,
            'service': None,
            'public_url': None,
            'internal_url': None,
            'admin_url': None,
            'cinder_service': 'cinder',
            'cinder_region': 'RegionOne',
            'cinder_public_url': 'http://cindernode1:8776/v1/$(tenant_id)s',
            'cinder_admin_url': 'http://cindernode1:8776/v1/$(tenant_id)s',
            'cinder_internal_url': 'http://cindernode1:8776/v1/$(tenant_id)s',
            'relation_id': None,
        }
        self.relation_set.assert_called_with(**expected)

    def test_identity_service_joined_no_api(self):
        'endpoint registration is skipped if api service is not enabled'
        self.config.side_effect = self.test_config.get
        self.service_enabled.return_value = False
        hooks.hooks.execute(['hooks/identity-service-relation-joined'])
        self.assertFalse(self.relation_set.called)
        self.service_enabled.assert_called_with('api')

    @patch.object(hooks, 'canonical_url')
    def test_identity_service_joined_icehouse(self, _canonical_url):
        'It properly requests unclustered endpoint via identity-service'
        self.os_release.return_value = 'icehouse'
        self.config.side_effect = self.test_config.get
        _canonical_url.return_value = 'http://cindernode1'
        hooks.hooks.execute(['hooks/identity-service-relation-joined'])
        expected = {
            'region': None,
            'service': None,
            'public_url': None,
            'internal_url': None,
            'admin_url': None,
            'cinder_service': 'cinder',
            'cinder_region': 'RegionOne',
            'cinder_public_url': 'http://cindernode1:8776/v1/$(tenant_id)s',
            'cinder_admin_url': 'http://cindernode1:8776/v1/$(tenant_id)s',
            'cinder_internal_url': 'http://cindernode1:8776/v1/$(tenant_id)s',
            'cinderv2_service': 'cinderv2',
            'cinderv2_region': 'RegionOne',
            'cinderv2_public_url': 'http://cindernode1:8776/v2/$(tenant_id)s',
            'cinderv2_admin_url': 'http://cindernode1:8776/v2/$(tenant_id)s',
            'cinderv2_internal_url': 'http://cindernode1:8776/'
                                     'v2/$(tenant_id)s',
            'relation_id': None,
        }
        self.relation_set.assert_called_with(**expected)

    @patch.object(hooks, 'canonical_url')
    def test_identity_service_joined_pike(self, _canonical_url):
        'It properly requests unclustered endpoint via identity-service'
        self.os_release.return_value = 'pike'
        self.config.side_effect = self.test_config.get
        _canonical_url.return_value = 'http://cindernode1'
        hooks.hooks.execute(['hooks/identity-service-relation-joined'])
        expected = {
            'cinderv2_service': 'cinderv2',
            'cinderv2_region': 'RegionOne',
            'cinderv2_public_url': 'http://cindernode1:8776/v2/$(tenant_id)s',
            'cinderv2_admin_url': 'http://cindernode1:8776/v2/$(tenant_id)s',
            'cinderv2_internal_url': 'http://cindernode1:8776/'
                                     'v2/$(tenant_id)s',
            'cinderv3_service': 'cinderv3',
            'cinderv3_region': 'RegionOne',
            'cinderv3_public_url': 'http://cindernode1:8776/v3/$(tenant_id)s',
            'cinderv3_admin_url': 'http://cindernode1:8776/v3/$(tenant_id)s',
            'cinderv3_internal_url': 'http://cindernode1:8776/'
                                     'v3/$(tenant_id)s',
            'relation_id': None,
        }
        self.relation_set.assert_called_with(**expected)

    @patch.object(hooks, 'canonical_url')
    def test_identity_service_joined_xena(self, _canonical_url):
        'It properly requests unclustered endpoint via identity-service'
        self.os_release.return_value = 'xena'
        self.config.side_effect = self.test_config.get
        _canonical_url.return_value = 'http://cindernode1'
        hooks.hooks.execute(['hooks/identity-service-relation-joined'])
        expected = {
            'cinderv3_service': 'cinderv3',
            'cinderv3_region': 'RegionOne',
            'cinderv3_public_url': 'http://cindernode1:8776/v3/$(tenant_id)s',
            'cinderv3_admin_url': 'http://cindernode1:8776/v3/$(tenant_id)s',
            'cinderv3_internal_url': 'http://cindernode1:8776/'
                                     'v3/$(tenant_id)s',
            'relation_id': None,
        }
        self.relation_set.assert_called_with(**expected)

    @patch('charmhelpers.contrib.openstack.ip.config')
    @patch('charmhelpers.contrib.openstack.ip.unit_get')
    @patch('charmhelpers.contrib.openstack.ip.is_clustered')
    def test_identity_service_joined_public_name(self, _is_clustered,
                                                 _unit_get, _config):
        self.os_release.return_value = 'icehouse'
        _unit_get.return_value = 'cindernode1'
        self.config.side_effect = self.test_config.get
        _config.side_effect = self.test_config.get
        self.test_config.set('os-public-hostname', 'public.example.com')
        _is_clustered.return_value = False
        hooks.hooks.execute(['hooks/identity-service-relation-joined'])
        v1_url = 'http://public.example.com:8776/v1/$(tenant_id)s'
        v2_url = 'http://public.example.com:8776/v2/$(tenant_id)s'
        expected = {
            'region': None,
            'service': None,
            'public_url': None,
            'internal_url': None,
            'admin_url': None,
            'cinder_service': 'cinder',
            'cinder_region': 'RegionOne',
            'cinder_public_url': v1_url,
            'cinder_admin_url': 'http://cindernode1:8776/v1/$(tenant_id)s',
            'cinder_internal_url': 'http://cindernode1:8776/v1/$(tenant_id)s',
            'cinderv2_service': 'cinderv2',
            'cinderv2_region': 'RegionOne',
            'cinderv2_public_url': v2_url,
            'cinderv2_admin_url': 'http://cindernode1:8776/v2/$(tenant_id)s',
            'cinderv2_internal_url': ('http://cindernode1:8776/'
                                      'v2/$(tenant_id)s'),
            'relation_id': None,
        }
        self.relation_set.assert_called_with(**expected)

    @patch('os.mkdir')
    def test_ceph_joined(self, mkdir):
        'It correctly prepares for a ceph changed hook'
        with patch('os.path.isdir') as isdir:
            isdir.return_value = False
            hooks.hooks.execute(['hooks/ceph-relation-joined'])
            mkdir.assert_called_with('/etc/ceph')
        self.apt_install.assert_called_with('ceph-common', fatal=True)

    def test_ceph_changed_no_key(self):
        'It does nothing when ceph key is not available'
        self.CONFIGS.complete_contexts.return_value = ['']
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        m = 'ceph relation incomplete. Peer not ready?'
        self.juju_log.assert_called_with(m)

    @patch.object(hooks, 'get_ceph_request')
    def test_ceph_changed_broker_send_rq(self, mget_ceph_request):
        mget_ceph_request.return_value = 'cephrq'
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = True
        self.is_request_complete.return_value = False
        self.ceph_config_file.return_value = '/var/lib/charm/cinder/ceph.conf'
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        self.ensure_ceph_keyring.assert_called_with(service='cinder',
                                                    user='cinder',
                                                    group='cinder')
        self.send_request_if_needed.assert_called_with('cephrq')
        for c in [call('/var/lib/charm/cinder/ceph.conf'),
                  call('/etc/cinder/cinder.conf')]:
            self.assertNotIn(c, self.CONFIGS.write.call_args_list)

    @patch('charmhelpers.core.host.service')
    @patch.object(hooks, "get_ceph_request")
    def test_ceph_changed_broker_success(self,
                                         mock_get_ceph_request,
                                         _service):
        'It ensures ceph assets created on ceph changed'
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = True
        self.ceph_config_file.return_value = '/var/lib/charm/cinder/ceph.conf'
        self.relation_get.return_value = {'broker_rsp':
                                          json.dumps({'exit-code': 0})}
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        self.ensure_ceph_keyring.assert_called_with(service='cinder',
                                                    user='cinder',
                                                    group='cinder')
        for c in [call('/var/lib/charm/cinder/ceph.conf'),
                  call('/etc/cinder/cinder.conf')]:
            self.assertIn(c, self.CONFIGS.write.call_args_list)
        self.service_restart.assert_called_with('cinder-volume')

    @patch.object(hooks, "get_ceph_request")
    def test_ceph_changed_broker_nonzero_rc(self, mock_get_ceph_request):
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = True
        self.ceph_config_file.return_value = '/var/lib/charm/cinder/ceph.conf'
        self.is_request_complete.return_value = False
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        self.ensure_ceph_keyring.assert_called_with(service='cinder',
                                                    user='cinder',
                                                    group='cinder')
        for c in [call('/var/lib/charm/cinder/ceph.conf'),
                  call('/etc/cinder/cinder.conf')]:
            self.assertNotIn(c, self.CONFIGS.write.call_args_list)

    def test_ceph_changed_no_keys(self):
        'It ensures ceph assets created on ceph changed'
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = False
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        # NOTE(jamespage): If ensure_ceph keyring fails, then
        # the hook should just exit 0 and return.
        self.assertTrue(self.juju_log.called)
        self.assertFalse(self.CONFIGS.write.called)

    def test_ceph_broken(self):
        self.service_name.return_value = 'cinder'
        with patch.object(hooks, 'CEPH_CONF', new="/some/random/file"):
            hooks.hooks.execute(['hooks/ceph-relation-broken'])
        self.delete_keyring.assert_called_with(service='cinder')
        self.assertTrue(self.CONFIGS.write_all.called)
        self.remove_alternative.assert_called_with(
            os.path.basename("/some/random/file"),
            self.ceph_config_file())

    def test_ceph_changed_no_leadership(self):
        '''It does not attempt to create ceph pool if not leader'''
        self.is_elected_leader.return_value = False
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = True
        hooks.hooks.execute(['hooks/ceph-relation-changed'])

    def test_ha_joined(self):
        self.generate_ha_relation_data.return_value = {'rel_data': 'data'}
        hooks.hooks.execute(['hooks/ha-relation-joined'])
        self.relation_set.assert_called_once_with(
            relation_id=None, rel_data='data')


class TestDepartedHooks(CharmTestCase):

    def setUp(self):
        super(TestDepartedHooks, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get_all

    def test_amqp_departed(self):
        self.CONFIGS.complete_contexts.return_value = ['amqp']
        hooks.hooks.execute(['hooks/amqp-relation-departed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')

    def test_amqp_departed_incomplete(self):
        self.CONFIGS.complete_contexts.return_value = []
        hooks.hooks.execute(['hooks/amqp-relation-departed'])
        assert not self.CONFIGS.write.called
        assert self.juju_log.called
