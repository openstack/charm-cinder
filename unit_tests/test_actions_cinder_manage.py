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

TO_PATCH = [
    'subprocess',
    'action_get',
    'action_fail',
    'action_set',
    'os_release',
]

SERVICE_LIST = """2018-09-07 09:56:08.469 29766 WARNING oslo_db.sqlalchemy.engines [req-80f93eba-3e8f-4c4f-af66-491f91e5821d - - - - -] URL mysql://cinder:***@10.5.0.39/cinder does not contain a '+drivername' portion, and will make use of a default driver.  A full dbname+drivername:// protocol is recommended. For MySQL, it is strongly recommended that mysql+pymysql:// be specified for maximum service compatibility
Binary           Host                                 Zone             Status     State Updated At           RPC Version  Object Version  Cluster
cinder-volume    juju-d15fa6-py3-upgrade-testing-9@LVM nova             enabled    XXX   2018-09-06 08:55:13  3.15         1.35
cinder-scheduler juju-d15fa6-py3-upgrade-testing-9    nova             enabled    XXX   2018-09-06 08:55:12  3.10         1.35
cinder-volume    cinder@cinder-ceph                   nova             enabled    :-)   2018-09-07 09:56:02  3.16         1.37
cinder-scheduler cinder                               nova             enabled    :-)   2018-09-07 09:56:02  3.11         1.37
cinder-volume    juju-d15fa6-py3-upgrade-testing-30@LVM nova             enabled    XXX   2018-09-06 09:30:40  3.15         1.35
cinder-scheduler juju-d15fa6-py3-upgrade-testing-30   nova             enabled    XXX   2018-09-06 09:30:47  3.10         1.35
cinder-volume    juju-d15fa6-py3-upgrade-testing-32@LVM nova             enabled    XXX   2018-09-06 15:04:13  3.16         1.37
cinder-scheduler juju-d15fa6-py3-upgrade-testing-32   nova             enabled    XXX   2018-09-06 15:04:21  3.11         1.37
cinder-volume    juju-d15fa6-py3-upgrade-testing-31@LVM nova             enabled    XXX   2018-09-06 15:04:15  3.16         1.37
cinder-scheduler juju-d15fa6-py3-upgrade-testing-31   nova             enabled    XXX   2018-09-06 15:04:35  3.11         1.37
"""  # noqa


class CinderManageTestCase(CharmTestCase):

    def setUp(self):
        super(CinderManageTestCase, self).setUp(cinder_manage, TO_PATCH)

    def test_cinder_manage_remove(self):
        cinder_manage.cinder_manage_remove('mybin', 'myhost')
        self.subprocess.check_call.assert_called_once_with(
            ['cinder-manage', 'service', 'remove', 'mybin', 'myhost'])

    def test_manage_volume_update_host(self):
        cinder_manage.cinder_manage_remove('host', 'host@this#that')
        self.subprocess.check_call.assert_called_once_with(
            ['cinder-manage', 'service', 'remove', 'host', 'host@this#that'])

    @patch.object(cinder_manage, 'cinder_manage_service_list')
    @patch.object(cinder_manage, 'cinder_manage_remove')
    def test_remove_services(self, cinder_manage_remove,
                             cinder_manage_service_list):
        self.action_get.return_value = 'svc1host'
        svc1_mock = mock.MagicMock()
        svc1_mock.binary = "svc1bin"
        svc1_mock.host = "svc1host"
        self.os_release.return_value = 'liberty'
        cinder_manage_service_list.return_value = [svc1_mock]
        cinder_manage.remove_services('arg')
        cinder_manage_remove.assert_called_once_with('svc1bin', 'svc1host')
        self.action_set.assert_called_once_with({'removed': 'svc1host'})

    @patch.object(cinder_manage, 'cinder_manage_service_list')
    @patch.object(cinder_manage, 'cinder_manage_remove')
    def test_remove_services_kilo(self, cinder_manage_remove,
                                  cinder_manage_service_list):
        self.action_get.return_value = 'svc1host'
        svc1_mock = mock.MagicMock()
        svc1_mock.binary = "svc1bin"
        svc1_mock.host = "svc1host"
        svc1_mock.id = 42
        self.os_release.return_value = 'kilo'
        cinder_manage_service_list.return_value = [svc1_mock]
        cinder_manage.remove_services('arg')
        self.action_fail.assert_called_once()

    @patch.object(cinder_manage, 'cinder_manage_service_list')
    @patch.object(cinder_manage, 'cinder_manage_remove')
    def test_remove_services_fail(self, cinder_manage_remove,
                                  cinder_manage_service_list):
        cinder_manage_remove.side_effect = Exception()
        self.action_get.return_value = 'svc1host'
        svc1_mock = mock.MagicMock()
        svc1_mock.binary = "svc1bin"
        svc1_mock.host = "svc1host"
        self.os_release.return_value = 'liberty'
        cinder_manage_service_list.return_value = [svc1_mock]
        cinder_manage.remove_services('arg')
        cinder_manage_remove.assert_called_once_with('svc1bin', 'svc1host')
        self.action_fail.assert_called_once_with(
            'Cannot remove service: svc1host')

    @patch.object(cinder_manage, 'cinder_manage_service_list')
    @patch.object(cinder_manage, 'cinder_manage_volume_update_host')
    def test__rename_volume_host(self, cinder_manage_volume_update_host,
                                 cinder_manage_service_list):
        self.action_get.return_value = 'myhost'
        svc1_mock = mock.MagicMock()
        svc1_mock.binary = "cinder-volume"
        svc1_mock.host = "b"
        cinder_manage_service_list.return_value = [svc1_mock]
        cinder_manage._rename_volume_host('a', 'b')
        cinder_manage_volume_update_host.assert_called_once_with('a', 'b')

    @patch.object(cinder_manage, 'cinder_manage_service_list')
    @patch.object(cinder_manage, 'cinder_manage_volume_update_host')
    def test__rename_volume_host_missing(self,
                                         cinder_manage_volume_update_host,
                                         cinder_manage_service_list):
        self.action_get.return_value = 'myhost'
        cinder_manage_service_list.return_value = []
        cinder_manage._rename_volume_host('a', 'b')
        self.assertFalse(cinder_manage_volume_update_host.called)
        self.action_fail.assert_called_once_with(
            'Cannot update host attribute to b, b not found')

    @patch.object(cinder_manage, 'cinder_manage_service_list')
    @patch.object(cinder_manage, 'cinder_manage_volume_update_host')
    def test__rename_volume_host_fail(self,
                                      cinder_manage_volume_update_host,
                                      cinder_manage_service_list):
        cinder_manage_volume_update_host.side_effect = Exception()
        self.action_get.return_value = 'myhost'
        svc1_mock = mock.MagicMock()
        svc1_mock.binary = "cinder-volume"
        svc1_mock.host = "b"
        cinder_manage_service_list.return_value = [svc1_mock]
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

    @patch.object(cinder_manage, 'subprocess')
    def test_cinder_manage_service_list(self, subprocess):
        subprocess.check_output.return_value = SERVICE_LIST.encode()
        self.assertEqual(len(cinder_manage.cinder_manage_service_list()), 10)
