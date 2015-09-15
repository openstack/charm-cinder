from mock import patch
import os

os.environ['JUJU_UNIT_NAME'] = 'cinder'

with patch('cinder_utils.register_configs') as register_configs:
    import openstack_upgrade

from test_utils import (
    CharmTestCase
)

TO_PATCH = [
    'config_changed',
    'do_action_openstack_upgrade',
    'register_configs',
    'relation_set',
    'relation_ids',
    'uuid'
]


class TestCinderUpgradeActions(CharmTestCase):

    def setUp(self):
        super(TestCinderUpgradeActions, self).setUp(openstack_upgrade,
                                                    TO_PATCH)

    @patch('charmhelpers.contrib.openstack.utils.config')
    @patch('charmhelpers.contrib.openstack.utils.action_set')
    def test_openstack_upgrade_true(self, action_set, config):
        self.do_action_openstack_upgrade.return_value = True
        self.relation_ids.return_value = ['relid1']
        self.uuid.uuid4.return_value = 12345

        openstack_upgrade.openstack_upgrade()

        self.assertTrue(self.do_action_openstack_upgrade.called)
        self.assertTrue(self.relation_ids.called)
        self.relation_set.assert_called_with(relation_id='relid1',
                                             upgrade_nonce=12345)
        self.assertTrue(self.config_changed.called)

    @patch('charmhelpers.contrib.openstack.utils.config')
    @patch('charmhelpers.contrib.openstack.utils.action_set')
    def test_openstack_upgrade_false(self, action_set, config):
        self.do_action_openstack_upgrade.return_value = False

        openstack_upgrade.openstack_upgrade()

        self.assertTrue(self.do_action_openstack_upgrade.called)
        self.assertFalse(self.relation_ids.called)
        self.assertFalse(self.relation_set.called)
        self.assertFalse(self.config_changed.called)
