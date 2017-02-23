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

from mock import patch, mock
from test_utils import (
    CharmTestCase
)
import cinder_manage
import cinder

TO_PATCH = [
    'subprocess',
    'action_get',
    'action_fail',
    'action_set',
    'os_release',
]


class CinderManageTestCase(CharmTestCase):

    def setUp(self):
        super(CinderManageTestCase, self).setUp(cinder_manage, TO_PATCH)

    def tearDown(self):
        cinder.reset_mock()
        cinder.context.reset_mock()
        cinder.db.reset_mock()
        cinder.db.sqlalchemy.reset_mock()
        cinder.db.sqlalchemy.api.reset_mock()

    def test_load_config_file(self):
        cinder_manage.load_config_file('/cinder.conf')
        cinder.flags.FLAGS.assert_called_once_with(
            args=[],
            default_config_files=['/cinder.conf'],
            project='cinder')

    def test_cinder_manage_remove(self):
        cinder_manage.cinder_manage_remove('mybin', 'myhost')
        self.subprocess.check_call.assert_called_once_with(
            ['cinder-manage', 'service', 'remove', 'mybin', 'myhost'])

    def test_manage_volume_update_host(self):
        cinder_manage.cinder_manage_remove('host', 'host@this#that')
        self.subprocess.check_call.assert_called_once_with(
            ['cinder-manage', 'service', 'remove', 'host', 'host@this#that'])

    @patch.object(cinder_manage, 'cinder_manage_remove')
    def test_remove_services(self, cinder_manage_remove):
        self.action_get.return_value = 'sv1host'
        svc1_mock = mock.MagicMock()
        svc1_mock.binary = "svc1bin"
        svc1_mock.host = "svc1host"
        query_mock = mock.MagicMock()
        query_mock.filter().all.return_value = [svc1_mock]
        cinder.db.sqlalchemy.api.model_query.return_value = query_mock
        self.os_release.return_value = 'liberty'
        cinder_manage.remove_services('arg')
        cinder_manage_remove.assert_called_once_with('svc1bin', 'svc1host')
        self.action_set.assert_called_once_with({'removed': 'svc1host'})

    @patch.object(cinder_manage, 'cinder_manage_remove')
    def test_remove_services_kilo(self, cinder_manage_remove):
        self.action_get.return_value = 'sv1host'
        svc1_mock = mock.MagicMock()
        svc1_mock.binary = "svc1bin"
        svc1_mock.host = "svc1host"
        svc1_mock.id = 42
        cinder.context.get_admin_context.return_value = 'admctxt'
        query_mock = mock.MagicMock()
        query_mock.filter().all.return_value = [svc1_mock]
        cinder.db.sqlalchemy.api.model_query.return_value = query_mock
        self.os_release.return_value = 'kilo'
        cinder_manage.remove_services('arg')
        cinder.db.service_destroy.assert_called_once_with('admctxt', 42)
        self.action_set.assert_called_once_with({'removed': 'svc1host'})

    @patch.object(cinder_manage, 'cinder_manage_remove')
    def test_remove_services_fail(self, cinder_manage_remove):
        cinder_manage_remove.side_effect = Exception()
        self.action_get.return_value = 'sv1host'
        svc1_mock = mock.MagicMock()
        svc1_mock.binary = "svc1bin"
        svc1_mock.host = "svc1host"
        query_mock = mock.MagicMock()
        query_mock.filter().all.return_value = [svc1_mock]
        cinder.db.sqlalchemy.api.model_query.return_value = query_mock
        self.os_release.return_value = 'liberty'
        cinder_manage.remove_services('arg')
        cinder_manage_remove.assert_called_once_with('svc1bin', 'svc1host')
        self.action_fail.assert_called_once_with(
            'Cannot remove service: svc1host')

    @patch.object(cinder_manage, 'cinder_manage_volume_update_host')
    def test__rename_volume_host(self, cinder_manage_volume_update_host):
        self.action_get.return_value = 'myhost'
        query_mock = mock.MagicMock()
        query_mock.filter().all.return_value = ['myhost']
        cinder.db.sqlalchemy.api.model_query.return_value = query_mock
        cinder.db.sqlalchemy.api.model_query.return_value = query_mock
        cinder_manage._rename_volume_host('a', 'b')
        cinder_manage_volume_update_host.assert_called_once_with('a', 'b')

    @patch.object(cinder_manage, 'cinder_manage_volume_update_host')
    def test__rename_volume_host_missing(self,
                                         cinder_manage_volume_update_host):
        self.action_get.return_value = 'myhost'
        query_mock = mock.MagicMock()
        query_mock.filter().all.return_value = []
        cinder.db.sqlalchemy.api.model_query.return_value = query_mock
        cinder_manage._rename_volume_host('a', 'b')
        self.assertFalse(cinder_manage_volume_update_host.called)
        self.action_fail.assert_called_once_with(
            'Cannot update host attribute from a, a not found')

    @patch.object(cinder_manage, 'cinder_manage_volume_update_host')
    def test__rename_volume_host_fail(self,
                                      cinder_manage_volume_update_host):
        cinder_manage_volume_update_host.side_effect = Exception()
        self.action_get.return_value = 'myhost'
        query_mock = mock.MagicMock()
        query_mock.filter().all().return_value = ['myhost']
        cinder.db.sqlalchemy.api.model_query.return_value = query_mock
        cinder_manage._rename_volume_host('a', 'b')
        cinder_manage_volume_update_host.assert_called_once_with('a', 'b')
        self.action_fail.assert_called_once_with('Cannot update host a')

    @patch.object(cinder_manage, '_rename_volume_host')
    def test_rename_volume_host(self, _rename_volume_host):
        self.action_get.return_value = {
            'currenthost': 'orghost',
            'newhost': 'newhost'}
        cinder_manage.rename_volume_host('arg')
        _rename_volume_host.assert_called_once_with('orghost', 'newhost')

    @patch.object(cinder_manage, '_rename_volume_host')
    def test_volume_host_add_driver(self, _rename_volume_host):
        self.action_get.return_value = {
            'currenthost': 'orghost',
            'driver': 'lvmdriver-1',
            'volume-backend-name': 'LVM'}
        cinder_manage.volume_host_add_driver('arg')
        _rename_volume_host.assert_called_once_with(
            'orghost', 'orghost@lvmdriver-1#LVM')

    @patch.object(cinder_manage, '_rename_volume_host')
    def test_volume_host_add_driver_novol_backend(self, _rename_volume_host):
        self.action_get.return_value = {
            'currenthost': 'orghost',
            'driver': 'lvmdriver-1',
            'volume-backend-name': ''}
        cinder_manage.volume_host_add_driver('arg')
        _rename_volume_host.assert_called_once_with(
            'orghost', 'orghost@lvmdriver-1')
