import os
from mock import MagicMock, patch, call

os.environ['JUJU_UNIT_NAME'] = 'cinder'
import cinder_utils as utils

# Need to do some early patching to get the module loaded.
# _restart_map = utils.restart_map
_register_configs = utils.register_configs
_service_enabled = utils.service_enabled
utils.register_configs = MagicMock()
utils.service_enabled = MagicMock()

import cinder_hooks as hooks

# Unpatch it now that its loaded.
utils.register_configs = _register_configs
utils.service_enabled = _service_enabled

from test_utils import (
    CharmTestCase,
    RESTART_MAP,
)

TO_PATCH = [
    # cinder_utils
    'determine_packages',
    'ensure_ceph_keyring',
    'ensure_ceph_pool',
    'juju_log',
    'lsb_release',
    'migrate_database',
    'configure_lvm_storage',
    'register_configs',
    'service_enabled',
    'set_ceph_env_variables',
    'CONFIGS',
    'CLUSTER_RES',
    # charmhelpers.core.hookenv
    'config',
    'relation_set',
    'relation_get',
    'relation_ids',
    'service_name',
    'unit_get',
    # charmhelpers.core.host
    'apt_install',
    'apt_update',
    # charmhelpers.contrib.openstack.openstack_utils
    'configure_installation_source',
    # charmhelpers.contrib.hahelpers.cluster_utils
    'eligible_leader',
    'get_hacluster_config',
    'is_leader',
    # charmhelpers.contrib.network.ip
    'get_iface_for_address',
    'get_netmask_for_address'
]


class TestClusterHooks(CharmTestCase):

    def setUp(self):
        super(TestClusterHooks, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get_all

    @patch('charmhelpers.core.host.service')
    @patch('charmhelpers.core.host.file_hash')
    def test_cluster_hook(self, file_hash, service):
        'Ensure API restart before haproxy on cluster changed'
        # set first hash lookup on all files
        side_effects = []
        # set first hash lookup on all configs in restart_on_change
        [side_effects.append('foo') for f in RESTART_MAP.keys()]
        # set second hash lookup on all configs in restart_on_change
        [side_effects.append('bar') for f in RESTART_MAP.keys()]
        file_hash.side_effect = side_effects
        hooks.hooks.execute(['hooks/cluster-relation-changed'])
        ex = [
            call('stop', 'cinder-api'),
            call('stop', 'cinder-volume'),
            call('stop', 'cinder-scheduler'),
            call('stop', 'haproxy'),
            call('stop', 'apache2'),
            call('start', 'cinder-api'),
            call('start', 'cinder-volume'),
            call('start', 'cinder-scheduler'),
            call('start', 'haproxy'),
            call('start', 'apache2')]
        self.assertEquals(ex, service.call_args_list)

    def test_ha_joined_complete_config(self):
        'Ensure hacluster subordinate receives all relevant config'
        conf = {
            'ha-bindiface': 'eth100',
            'ha-mcastport': '37373',
            'vip': '192.168.25.163',
            'vip_iface': 'eth101',
            'vip_cidr': '19',
        }
        self.get_hacluster_config.return_value = conf
        self.get_iface_for_address.return_value = 'eth101'
        self.get_netmask_for_address.return_value = '255.255.224.0'
        hooks.hooks.execute(['hooks/ha-relation-joined'])
        ex_args = {
            'corosync_mcastport': '37373',
            'init_services': {'res_cinder_haproxy': 'haproxy'},
            'resource_params': {
                'res_cinder_eth101_vip':
                'params ip="192.168.25.163" cidr_netmask="255.255.224.0"'
                ' nic="eth101"',
                'res_cinder_haproxy': 'op monitor interval="5s"'
            },
            'corosync_bindiface': 'eth100',
            'clones': {'cl_cinder_haproxy': 'res_cinder_haproxy'},
            'resources': {
                'res_cinder_eth101_vip': 'ocf:heartbeat:IPaddr2',
                'res_cinder_haproxy': 'lsb:haproxy'
            }
        }
        self.relation_set.assert_called_with(**ex_args)

    @patch.object(hooks, 'identity_joined')
    def test_ha_changed_clustered_not_leader(self, joined):
        'Skip keystone notification if not cluster leader'
        self.relation_get.return_value = True
        self.is_leader.return_value = False
        hooks.hooks.execute(['hooks/ha-relation-changed'])
        self.assertFalse(joined.called)

    @patch.object(hooks, 'identity_joined')
    def test_ha_changed_clustered_leader(self, joined):
        'Notify keystone if cluster leader'
        self.relation_get.return_value = True
        self.is_leader.return_value = True
        self.relation_ids.return_value = ['identity:0']
        hooks.hooks.execute(['hooks/ha-relation-changed'])
        joined.assert_called_with(rid='identity:0')

    def test_ha_changed_not_clustered(self):
        'Ensure ha_changed exits early if not yet clustered'
        self.relation_get.return_value = None
        hooks.hooks.execute(['hooks/ha-relation-changed'])
        self.assertTrue(self.juju_log.called)
        self.assertFalse(self.is_leader.called)
