import os

from mock import patch

from test_utils import (
    CharmTestCase,
    RESTART_MAP
)

os.environ['JUJU_UNIT_NAME'] = 'cinder'
with patch('cinder_utils.register_configs') as register_configs:
    with patch('cinder_utils.restart_map') as restart_map:
        restart_map.return_value = RESTART_MAP
        import openstack_upgrade

TO_PATCH = [
    'config_changed',
    'do_openstack_upgrade',
    'relation_set',
    'relation_ids',
    'uuid'
]


class TestCinderUpgradeActions(CharmTestCase):

    def setUp(self):
        super(TestCinderUpgradeActions, self).setUp(openstack_upgrade,
                                                    TO_PATCH)

    @patch('charmhelpers.contrib.openstack.utils.juju_log')
    @patch('charmhelpers.contrib.openstack.utils.config')
    @patch('charmhelpers.contrib.openstack.utils.action_set')
    @patch('charmhelpers.contrib.openstack.utils.git_install_requested')
    @patch('charmhelpers.contrib.openstack.utils.openstack_upgrade_available')
    def test_openstack_upgrade_true(self, upgrade_avail, git_requested,
                                    action_set, config, log):
        git_requested.return_value = False
        upgrade_avail.return_value = True
        config.return_value = True
        self.relation_ids.return_value = ['relid1']
        self.uuid.uuid4.return_value = 12345

        openstack_upgrade.openstack_upgrade()

        self.assertTrue(self.do_openstack_upgrade.called)
        self.assertTrue(self.relation_ids.called)
        self.relation_set.assert_called_with(relation_id='relid1',
                                             upgrade_nonce=12345)
        self.assertTrue(self.config_changed.called)

    @patch('charmhelpers.contrib.openstack.utils.juju_log')
    @patch('charmhelpers.contrib.openstack.utils.config')
    @patch('charmhelpers.contrib.openstack.utils.action_set')
    @patch('charmhelpers.contrib.openstack.utils.git_install_requested')
    @patch('charmhelpers.contrib.openstack.utils.openstack_upgrade_available')
    def test_openstack_upgrade_false(self, upgrade_avail, git_requested,
                                     action_set, config, log):
        git_requested.return_value = False
        upgrade_avail.return_value = True
        config.return_value = False

        openstack_upgrade.openstack_upgrade()

        self.assertFalse(self.do_openstack_upgrade.called)
        self.assertFalse(self.relation_ids.called)
        self.assertFalse(self.relation_set.called)
        self.assertFalse(self.config_changed.called)
