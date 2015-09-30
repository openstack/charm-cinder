import json
from mock import (
    MagicMock,
    patch,
    call
)
import yaml

import cinder_utils as utils
from test_utils import (
    CharmTestCase,
    RESTART_MAP
)

# Need to do some early patching to get the module loaded.
_restart_map = utils.restart_map
_register_configs = utils.register_configs

utils.restart_map = MagicMock()
utils.restart_map.return_value = RESTART_MAP
utils.register_configs = MagicMock()

import cinder_hooks as hooks
hooks.hooks._config_save = False

hooks.hooks._config_save = False

# Unpatch it now that its loaded.
utils.restart_map = _restart_map
utils.register_configs = _register_configs

TO_PATCH = [
    'check_call',
    'send_request_if_needed',
    'is_request_complete',
    # cinder_utils
    'configure_lvm_storage',
    'determine_packages',
    'do_openstack_upgrade',
    'ensure_ceph_keyring',
    'git_install',
    'juju_log',
    'log',
    'lsb_release',
    'migrate_database',
    'register_configs',
    'restart_map',
    'service_enabled',
    'set_ceph_env_variables',
    'CONFIGS',
    'CLUSTER_RES',
    'ceph_config_file',
    'update_nrpe_config',
    # charmhelpers.core.hookenv
    'config',
    'is_relation_made',
    'local_unit',
    'relation_get',
    'relation_ids',
    'relation_set',
    'service_name',
    'unit_get',
    # charmhelpers.core.host
    'apt_install',
    'apt_update',
    'service_reload',
    'service_restart',
    # charmhelpers.contrib.openstack.openstack_utils
    'configure_installation_source',
    'openstack_upgrade_available',
    'os_release',
    # charmhelpers.contrib.hahelpers.cluster_utils
    'is_elected_leader',
    'get_hacluster_config',
    'execd_preinstall',
    'get_ipv6_addr',
    'sync_db_with_multi_ipv6_addresses',
    'delete_keyring'
]


class TestInstallHook(CharmTestCase):

    def setUp(self):
        super(TestInstallHook, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get

    @patch.object(utils, 'git_install_requested')
    def test_install_precise_distro(self, git_requested):
        'It redirects to cloud archive if setup to install precise+distro'
        git_requested.return_value = False
        self.lsb_release.return_value = {'DISTRIB_CODENAME': 'precise'}
        hooks.hooks.execute(['hooks/install'])
        ca = 'cloud:precise-folsom'
        self.configure_installation_source.assert_called_with(ca)

    @patch.object(utils, 'git_install_requested')
    def test_install_git(self, git_requested):
        git_requested.return_value = True
        self.determine_packages.return_value = ['foo', 'bar', 'baz']
        repo = 'cloud:trusty-juno'
        openstack_origin_git = {
            'repositories': [
                {'name': 'requirements',
                 'repository': 'git://git.openstack.org/openstack/requirements',  # noqa
                 'branch': 'stable/juno'},
                {'name': 'cinder',
                 'repository': 'git://git.openstack.org/openstack/cinder',
                 'branch': 'stable/juno'}
            ],
            'directory': '/mnt/openstack-git',
        }
        projects_yaml = yaml.dump(openstack_origin_git)
        self.test_config.set('openstack-origin', repo)
        self.test_config.set('openstack-origin-git', projects_yaml)
        hooks.hooks.execute(['hooks/install'])
        self.assertTrue(self.execd_preinstall.called)
        self.configure_installation_source.assert_called_with(repo)
        self.apt_update.assert_called_with()
        self.apt_install.assert_called_with(['foo', 'bar', 'baz'], fatal=True)
        self.git_install.assert_called_with(projects_yaml)

    @patch.object(utils, 'git_install_requested')
    def test_correct_install_packages(self, git_requested):
        'It installs the correct packages based on what is determined'
        git_requested.return_value = False
        self.determine_packages.return_value = ['foo', 'bar', 'baz']
        hooks.hooks.execute(['hooks/install'])
        self.apt_install.assert_called_with(['foo', 'bar', 'baz'], fatal=True)


class TestChangedHooks(CharmTestCase):

    def setUp(self):
        super(TestChangedHooks, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get

    @patch.object(hooks, 'amqp_joined')
    def test_upgrade_charm_no_amqp(self, _joined):
        self.relation_ids.return_value = []
        hooks.hooks.execute(['hooks/upgrade-charm'])
        _joined.assert_not_called()

    @patch.object(hooks, 'amqp_joined')
    def test_upgrade_charm_with_amqp(self, _joined):
        self.relation_ids.return_value = ['amqp:1']
        hooks.hooks.execute(['hooks/upgrade-charm'])
        _joined.assert_called_with(relation_id='amqp:1')

    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'git_install_requested')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed(self, config_val_changed,
                            git_requested, conf_https):
        'It writes out all config'
        git_requested.return_value = False
        self.openstack_upgrade_available.return_value = False
        hooks.hooks.execute(['hooks/config-changed'])
        self.assertTrue(self.CONFIGS.write_all.called)
        self.assertTrue(conf_https.called)
        self.configure_lvm_storage.assert_called_with(['sdb'],
                                                      'cinder-volumes',
                                                      False, False, False)

    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'git_install_requested')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_block_devices(self, config_val_changed,
                                          git_requested, conf_https):
        'It writes out all config'
        git_requested.return_value = False
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
    @patch.object(hooks, 'git_install_requested')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_uses_remove_missing_force(self,
                                                      config_val_changed,
                                                      git_requested,
                                                      conf_https):
        'It uses the remove-missing-force config option'
        git_requested.return_value = False
        self.openstack_upgrade_available.return_value = False
        self.test_config.set('block-device', 'sdb')
        self.test_config.set('remove-missing-force', True)
        hooks.hooks.execute(['hooks/config-changed'])
        self.configure_lvm_storage.assert_called_with(
            ['sdb'],
            'cinder-volumes',
            False, False, True)

    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'git_install_requested')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_upgrade_available(self, config_val_changed,
                                              git_requested, conf_https):
        'It writes out all config with an available OS upgrade'
        git_requested.return_value = False
        self.openstack_upgrade_available.return_value = True
        hooks.hooks.execute(['hooks/config-changed'])
        self.do_openstack_upgrade.assert_called_with(configs=self.CONFIGS)

    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'git_install_requested')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_git_updated(self, config_val_changed,
                                        git_requested, conf_https):
        git_requested.return_value = True
        repo = 'cloud:trusty-juno'
        openstack_origin_git = {
            'repositories': [
                {'name': 'requirements',
                 'repository': 'git://git.openstack.org/openstack/requirements',  # noqa
                 'branch': 'stable/juno'},
                {'name': 'cinder',
                 'repository': 'git://git.openstack.org/openstack/',
                 'branch': 'stable/juno'}
            ],
            'directory': '/mnt/openstack-git',
        }
        projects_yaml = yaml.dump(openstack_origin_git)
        self.test_config.set('openstack-origin', repo)
        self.test_config.set('openstack-origin-git', projects_yaml)
        hooks.hooks.execute(['hooks/config-changed'])
        self.git_install.assert_called_with(projects_yaml)
        self.assertFalse(self.do_openstack_upgrade.called)
        self.assertTrue(conf_https.called)

    @patch('charmhelpers.core.host.service')
    @patch.object(hooks, 'configure_https')
    @patch.object(hooks, 'git_install_requested')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_overwrite_changed(self, config_val_changed,
                                              git_requested, conf_https,
                                              _services):
        'It uses the overwrite config option'
        git_requested.return_value = False
        self.openstack_upgrade_available.return_value = False
        config_val_changed.return_value = True
        hooks.hooks.execute(['hooks/config-changed'])
        self.assertTrue(self.CONFIGS.write_all.called)
        self.assertTrue(conf_https.called)
        self.configure_lvm_storage.assert_called_with(['sdb'],
                                                      'cinder-volumes',
                                                      False, False, False)
        self.service_restart.assert_called_with('cinder-volume')

    @patch.object(hooks, 'git_install_requested')
    @patch.object(hooks, 'config_value_changed')
    def test_config_changed_with_openstack_upgrade_action(self,
                                                          config_value_changed,
                                                          git_requested):
        git_requested.return_value = False
        self.openstack_upgrade_available.return_value = True
        self.test_config.set('action-managed-upgrade', True)

        hooks.hooks.execute(['hooks/config-changed'])

        self.assertFalse(self.do_openstack_upgrade.called)

    def test_db_changed(self):
        'It writes out cinder.conf on db changed'
        self.relation_get.return_value = 'cinder/0 cinder/1'
        self.local_unit.return_value = 'cinder/0'
        self.CONFIGS.complete_contexts.return_value = ['shared-db']
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')
        self.assertTrue(self.migrate_database.called)

    def test_pgsql_db_changed(self):
        'It writes out cinder.conf on db changed'
        self.CONFIGS.complete_contexts.return_value = ['pgsql-db']
        hooks.hooks.execute(['hooks/pgsql-db-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')
        self.assertTrue(self.migrate_database.called)

    def test_db_changed_relation_incomplete(self):
        'It does not write out cinder.conf with incomplete shared-db rel'
        self.relation_get.return_value = 'cinder/0 cinder/1'
        self.local_unit.return_value = 'cinder/0'
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.assertFalse(self.CONFIGS.write.called)
        self.assertFalse(self.migrate_database.called)

    def test_db_changed_relation_db_no_acl(self):
        'It does not migration when acl entry not present'
        self.relation_get.return_value = 'cinder/1 cinder/2'
        self.local_unit.return_value = 'cinder/0'
        self.CONFIGS.complete_contexts.return_value = ['shared-db']
        self.is_elected_leader.return_value = True
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.assertFalse(self.migrate_database.called)

    def test_db_changed_relation_db_missing_acls(self):
        'No database migration is attempted when ACL list is not present'
        self.relation_get.return_value = None
        self.local_unit.return_value = 'cinder/0'
        self.CONFIGS.complete_contexts.return_value = ['shared-db']
        self.is_elected_leader.return_value = True
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.assertFalse(self.migrate_database.called)

    def test_pgsql_db_changed_relation_incomplete(self):
        'It does not write out cinder.conf with incomplete pgsql-db rel'
        hooks.hooks.execute(['hooks/pgsql-db-relation-changed'])
        self.assertFalse(self.CONFIGS.write.called)
        self.assertFalse(self.migrate_database.called)

    def test_db_changed_not_leader(self):
        'It does not migrate database when not leader'
        self.relation_get.return_value = 'cinder/0 cinder/1'
        self.local_unit.return_value = 'cinder/0'
        self.is_elected_leader.return_value = False
        self.CONFIGS.complete_contexts.return_value = ['shared-db']
        hooks.hooks.execute(['hooks/shared-db-relation-changed'])
        self.CONFIGS.write.assert_called_with('/etc/cinder/cinder.conf')
        self.assertFalse(self.migrate_database.called)

    def test_pgsql_db_changed_not_leader(self):
        'It does not migrate database when not leader'
        self.is_elected_leader.return_value = False
        self.CONFIGS.complete_contexts.return_value = ['pgsql-db']
        hooks.hooks.execute(['hooks/pgsql-db-relation-changed'])
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

    @patch.object(hooks, 'identity_joined')
    def test_configure_https_enable(self, identity_joined):
        'It enables https from hooks when we have https data'
        self.CONFIGS.complete_contexts.return_value = ['https']
        self.relation_ids.return_value = ['identity-service:0']
        hooks.configure_https()
        calls = [call('a2dissite', 'openstack_https_frontend'),
                 call('service', 'apache2', 'reload')]
        self.check_call.assert_called_has_calls(calls)
        identity_joined.assert_called_with(rid='identity-service:0')

    @patch.object(hooks, 'identity_joined')
    def test_configure_https_disable(self, identity_joined):
        'It enables https from hooks when we have https data'
        self.CONFIGS.complete_contexts.return_value = []
        self.relation_ids.return_value = ['identity-service:0']
        hooks.configure_https()
        calls = [call('a2dissite', 'openstack_https_frontend'),
                 call('service', 'apache2', 'reload')]
        self.check_call.assert_called_has_calls(calls)
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
        self.unit_get.return_value = 'cindernode1'
        self.is_relation_made.return_value = False
        hooks.hooks.execute(['hooks/shared-db-relation-joined'])
        expected = {'username': 'cinder',
                    'hostname': 'cindernode1', 'database': 'cinder'}
        self.relation_set.assert_called_with(**expected)

    def test_db_joined_with_ipv6(self):
        'It properly requests access to a shared-db service'
        self.unit_get.return_value = 'cindernode1'
        self.get_ipv6_addr.return_value = ['2001:db8:1::1']
        self.sync_db_with_multi_ipv6_addresses.return_value = MagicMock()
        self.is_relation_made.return_value = False
        self.test_config.set('prefer-ipv6', True)
        self.test_config.set('vip', 'dummy_vip')
        hooks.hooks.execute(['hooks/shared-db-relation-joined'])
        self.sync_db_with_multi_ipv6_addresses.assert_called_with_once(
            'cinder', 'cinder')

    def test_db_joined_with_postgresql(self):
        self.is_relation_made.return_value = True

        with self.assertRaises(Exception) as context:
            hooks.hooks.execute(['hooks/shared-db-relation-joined'])
        self.assertEqual(context.exception.message,
                         'Attempting to associate a mysql database when there '
                         'is already associated a postgresql one')

    def test_postgresql_db_joined(self):
        'It properly requests access to a postgresql-db service'
        self.unit_get.return_value = 'cindernode1'
        self.is_relation_made.return_value = False
        hooks.hooks.execute(['hooks/pgsql-db-relation-joined'])
        expected = {'database': 'cinder'}
        self.relation_set.assert_called_with(**expected)

    def test_postgresql_joined_with_db(self):
        self.is_relation_made.return_value = True

        with self.assertRaises(Exception) as context:
            hooks.hooks.execute(['hooks/pgsql-db-relation-joined'])
        self.assertEqual(context.exception.message,
                         'Attempting to associate a postgresql database when'
                         ' there is already associated a mysql one')

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
        self.unit_get.return_value = 'cindernode1'
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

    @patch.object(hooks, 'canonical_url')
    def test_identity_service_joined_icehouse(self, _canonical_url):
        'It properly requests unclustered endpoint via identity-service'
        self.os_release.return_value = 'icehouse'
        self.unit_get.return_value = 'cindernode1'
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

    @patch('charmhelpers.contrib.openstack.ip.config')
    @patch('charmhelpers.contrib.openstack.ip.unit_get')
    @patch('charmhelpers.contrib.openstack.ip.is_clustered')
    def test_identity_service_joined_public_name(self, _is_clustered,
                                                 _unit_get, _config):
        self.os_release.return_value = 'icehouse'
        self.unit_get.return_value = 'cindernode1'
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
        self.assertFalse(self.set_ceph_env_variables.called)

    @patch('charmhelpers.core.host.service')
    @patch("cinder_hooks.relation_get", autospec=True)
    def test_ceph_changed_broker_success(self, mock_relation_get,
                                         _service):
        'It ensures ceph assets created on ceph changed'
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = True
        self.ceph_config_file.return_value = '/var/lib/charm/cinder/ceph.conf'
        mock_relation_get.return_value = {'broker_rsp':
                                          json.dumps({'exit-code': 0})}
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        self.ensure_ceph_keyring.assert_called_with(service='cinder',
                                                    user='cinder',
                                                    group='cinder')
        for c in [call('/var/lib/charm/cinder/ceph.conf'),
                  call('/etc/cinder/cinder.conf')]:
            self.assertIn(c, self.CONFIGS.write.call_args_list)
        self.set_ceph_env_variables.assert_called_with(service='cinder')
        self.service_restart.assert_called_with('cinder-volume')

    def test_ceph_changed_broker_nonzero_rc(self):
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
        self.assertFalse(self.set_ceph_env_variables.called)

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
        hooks.hooks.execute(['hooks/ceph-relation-broken'])
        self.delete_keyring.assert_called_with(service='cinder')
        self.assertTrue(self.CONFIGS.write_all.called)

    def test_ceph_changed_no_leadership(self):
        '''It does not attempt to create ceph pool if not leader'''
        self.is_elected_leader.return_value = False
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = True
        hooks.hooks.execute(['hooks/ceph-relation-changed'])


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
