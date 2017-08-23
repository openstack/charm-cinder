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
import sys

from mock import patch, call, MagicMock

from test_utils import (
    CharmTestCase,
    RESTART_MAP,
)

os.environ['JUJU_UNIT_NAME'] = 'cinder'

# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
mock_apt = MagicMock()
sys.modules['apt'] = mock_apt
mock_apt.apt_pkg = MagicMock()


with patch('cinder_utils.register_configs') as register_configs:
    with patch('cinder_utils.restart_map') as restart_map:
        restart_map.return_value = RESTART_MAP
        import cinder_hooks as hooks

hooks.hooks._config_save = False

TO_PATCH = [
    # cinder_utils
    'determine_packages',
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
    # charmhelpers.core.host
    'apt_install',
    'apt_update',
    # charmhelpers.contrib.openstack.openstack_utils
    'configure_installation_source',
    # charmhelpers.contrib.hahelpers.cluster_utils
    'is_elected_leader',
    'get_hacluster_config',
    # charmhelpers.contrib.network.ip
    'get_iface_for_address',
    'get_netmask_for_address',
    'get_relation_ip',
]


class TestClusterHooks(CharmTestCase):

    def setUp(self):
        super(TestClusterHooks, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get

    @patch.object(hooks, 'check_local_db_actions_complete',
                  lambda *args, **kwargs: None)
    @patch('charmhelpers.core.host.service')
    @patch('charmhelpers.core.host.path_hash')
    def test_cluster_hook(self, path_hash, service):
        'Ensure API restart before haproxy on cluster changed'
        # set first hash lookup on all files
        side_effects = []
        # set first hash lookup on all configs in restart_on_change
        [side_effects.append('foo') for f in RESTART_MAP.keys()]
        # set second hash lookup on all configs in restart_on_change
        [side_effects.append('bar') for f in RESTART_MAP.keys()]
        path_hash.side_effect = side_effects
        hooks.hooks.execute(['hooks/cluster-relation-changed'])
        ex = [
            call('stop', 'cinder-api'),
            call('start', 'cinder-api'),
            call('stop', 'cinder-volume'),
            call('start', 'cinder-volume'),
            call('stop', 'cinder-scheduler'),
            call('start', 'cinder-scheduler'),
            call('stop', 'haproxy'),
            call('start', 'haproxy'),
            call('stop', 'apache2'),
            call('start', 'apache2'),
        ]
        self.assertEquals(ex, service.call_args_list)

    @patch.object(hooks, 'check_local_db_actions_complete',
                  lambda *args, **kwargs: None)
    def test_cluster_joined_hook(self):
        self.config.side_effect = self.test_config.get
        hooks.hooks.execute(['hooks/cluster-relation-joined'])
        self.assertTrue(self.relation_set.called)

    @patch.object(hooks, 'check_local_db_actions_complete',
                  lambda *args, **kwargs: None)
    def test_cluster_joined_hook_multinet(self):
        self.config.side_effect = self.test_config.get
        self.get_relation_ip.return_value = '10.1.1.1'
        hooks.hooks.execute(['hooks/cluster-relation-joined'])
        self.relation_set.assert_has_calls([
            call(relation_id=None, relation_settings={
                 'private-address': '10.1.1.1',
                 'admin-address': '10.1.1.1',
                 'internal-address': '10.1.1.1',
                 'public-address': '10.1.1.1'})
        ])

    def test_ha_joined_complete_config(self):
        'Ensure hacluster subordinate receives all relevant config'
        conf = {
            'ha-bindiface': 'eth100',
            'ha-mcastport': '37373',
            'vip': '192.168.25.163',
            'vip_iface': 'eth101',
            'vip_cidr': '19',
        }

        self.test_config.set('prefer-ipv6', 'False')
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
            'relation_id': None,
            'clones': {'cl_cinder_haproxy': 'res_cinder_haproxy'},
            'resources': {
                'res_cinder_eth101_vip': 'ocf:heartbeat:IPaddr2',
                'res_cinder_haproxy': 'lsb:haproxy'
            }
        }
        self.relation_set.assert_has_calls([
            call(relation_id=None,
                 groups={'grp_cinder_vips': 'res_cinder_eth101_vip'}),
            call(**ex_args)
        ])

    def test_ha_joined_complete_config_with_ipv6(self):
        'Ensure hacluster subordinate receives all relevant config'
        conf = {
            'ha-bindiface': 'eth100',
            'ha-mcastport': '37373',
            'vip': '2001:db8:1::1',
            'vip_iface': 'eth101',
            'vip_cidr': '64',
        }

        self.test_config.set('prefer-ipv6', 'True')
        self.get_hacluster_config.return_value = conf
        self.get_iface_for_address.return_value = 'eth101'
        self.get_netmask_for_address.return_value = 'ffff.ffff.ffff.ffff'
        hooks.hooks.execute(['hooks/ha-relation-joined'])
        ex_args = {
            'relation_id': None,
            'corosync_mcastport': '37373',
            'init_services': {'res_cinder_haproxy': 'haproxy'},
            'resource_params': {
                'res_cinder_eth101_vip':
                'params ipv6addr="2001:db8:1::1" '
                'cidr_netmask="ffff.ffff.ffff.ffff" '
                'nic="eth101"',
                'res_cinder_haproxy': 'op monitor interval="5s"'
            },
            'corosync_bindiface': 'eth100',
            'clones': {'cl_cinder_haproxy': 'res_cinder_haproxy'},
            'resources': {
                'res_cinder_eth101_vip': 'ocf:heartbeat:IPv6addr',
                'res_cinder_haproxy': 'lsb:haproxy'
            }
        }
        self.relation_set.assert_called_with(**ex_args)

    def test_ha_joined_no_bound_ip(self):
        '''
        Ensure fallback configuration options are used if network
        interface cannot be auto-detected
        '''
        conf = {
            'ha-bindiface': 'eth100',
            'ha-mcastport': '37373',
            'vip': '192.168.25.163',
        }

        self.test_config.set('prefer-ipv6', 'False')
        self.test_config.set('vip_iface', 'eth120')
        self.test_config.set('vip_cidr', '21')
        self.get_hacluster_config.return_value = conf
        self.get_iface_for_address.return_value = None
        self.get_netmask_for_address.return_value = None
        hooks.hooks.execute(['hooks/ha-relation-joined'])
        ex_args = {
            'relation_id': None,
            'corosync_mcastport': '37373',
            'init_services': {'res_cinder_haproxy': 'haproxy'},
            'resource_params': {
                'res_cinder_eth120_vip':
                'params ip="192.168.25.163" cidr_netmask="21"'
                ' nic="eth120"',
                'res_cinder_haproxy': 'op monitor interval="5s"'
            },
            'corosync_bindiface': 'eth100',
            'clones': {'cl_cinder_haproxy': 'res_cinder_haproxy'},
            'resources': {
                'res_cinder_eth120_vip': 'ocf:heartbeat:IPaddr2',
                'res_cinder_haproxy': 'lsb:haproxy'
            }
        }
        self.relation_set.assert_has_calls([
            call(relation_id=None,
                 groups={'grp_cinder_vips': 'res_cinder_eth120_vip'}),
            call(**ex_args)
        ])

    @patch.object(hooks, 'identity_joined')
    def test_ha_changed_clustered(self, joined):
        self.relation_get.return_value = True
        self.relation_ids.return_value = ['identity:0']
        hooks.hooks.execute(['hooks/ha-relation-changed'])
        joined.assert_called_with(rid='identity:0')

    def test_ha_changed_not_clustered(self):
        'Ensure ha_changed exits early if not yet clustered'
        self.relation_get.return_value = None
        hooks.hooks.execute(['hooks/ha-relation-changed'])
        self.assertTrue(self.juju_log.called)
