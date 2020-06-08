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

from unittest.mock import patch, call, MagicMock

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
    # charmhelpers.contrib.network.ip
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
        self.assertEqual(ex, service.call_args_list)

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
