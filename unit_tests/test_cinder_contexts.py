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

from test_utils import CharmTestCase
from mock import patch, MagicMock

import cinder_contexts as contexts

os.environ['JUJU_UNIT_NAME'] = 'cinder'
import cinder_utils as utils

TO_PATCH = [
    'config',
    'relation_ids',
    'service_name',
    'determine_apache_port',
    'determine_api_port',
    'get_os_codename_install_source',
    'related_units',
    'relation_get'
]


class TestCinderContext(CharmTestCase):

    def setUp(self):
        super(TestCinderContext, self).setUp(contexts, TO_PATCH)

    def test_glance_not_related(self):
        self.relation_ids.return_value = []
        self.assertEquals(contexts.ImageServiceContext()(), {})

    def test_glance_related(self):
        self.relation_ids.return_value = ['image-service:0']
        self.config.return_value = '1'
        self.assertEquals(contexts.ImageServiceContext()(),
                          {'glance_api_version': '1'})

    def test_glance_related_api_v2(self):
        self.relation_ids.return_value = ['image-service:0']
        self.config.return_value = '2'
        self.assertEquals(contexts.ImageServiceContext()(),
                          {'glance_api_version': '2'})

    def test_ceph_not_related(self):
        self.relation_ids.return_value = []
        self.assertEquals(contexts.CephContext()(), {})

    def test_ceph_related(self):
        self.relation_ids.return_value = ['ceph:0']
        self.get_os_codename_install_source.return_value = 'havana'
        service = 'mycinder'
        self.service_name.return_value = service
        self.assertEquals(
            contexts.CephContext()(),
            {'volume_driver': 'cinder.volume.driver.RBDDriver',
             'rbd_pool': service,
             'rbd_user': service,
             'host': service})

    def test_ceph_related_icehouse(self):
        self.relation_ids.return_value = ['ceph:0']
        self.get_os_codename_install_source.return_value = 'icehouse'
        service = 'mycinder'
        self.service_name.return_value = service
        self.assertEquals(
            contexts.CephContext()(),
            {'volume_driver': 'cinder.volume.drivers.rbd.RBDDriver',
             'rbd_pool': service,
             'rbd_user': service,
             'host': service})

    @patch.object(utils, 'service_enabled')
    def test_apache_ssl_context_service_disabled(self, service_enabled):
        service_enabled.return_value = False
        self.assertEquals(contexts.ApacheSSLContext()(), {})

    def test_storage_backend_no_backends(self):
        self.relation_ids.return_value = []
        self.assertEquals(contexts.StorageBackendContext()(), {})

    def test_storage_backend_single_backend(self):
        self.relation_ids.return_value = ['cinder-ceph:0']
        self.related_units.return_value = ['cinder-ceph/0']
        self.relation_get.return_value = 'cinder-ceph'
        self.assertEquals(contexts.StorageBackendContext()(),
                          {'backends': 'cinder-ceph'})

    def test_storage_backend_multi_backend(self):
        self.relation_ids.return_value = ['cinder-ceph:0', 'cinder-vmware:0']
        self.related_units.side_effect = [['cinder-ceph/0'],
                                          ['cinder-vmware/0']]
        self.relation_get.side_effect = ['cinder-ceph', 'cinder-vmware']
        self.assertEquals(contexts.StorageBackendContext()(),
                          {'backends': 'cinder-ceph,cinder-vmware'})

    mod_ch_context = 'charmhelpers.contrib.openstack.context'

    @patch('%s.ApacheSSLContext.canonical_names' % (mod_ch_context))
    @patch('%s.ApacheSSLContext.configure_ca' % (mod_ch_context))
    @patch('%s.config' % (mod_ch_context))
    @patch('%s.is_clustered' % (mod_ch_context))
    @patch('%s.determine_apache_port' % (mod_ch_context))
    @patch('%s.determine_api_port' % (mod_ch_context))
    @patch('%s.unit_get' % (mod_ch_context))
    @patch('%s.https' % (mod_ch_context))
    @patch.object(utils, 'service_enabled')
    def test_apache_ssl_context_service_enabled(self, service_enabled,
                                                mock_https, mock_unit_get,
                                                mock_determine_api_port,
                                                mock_determine_apache_port,
                                                mock_is_clustered,
                                                mock_hookenv,
                                                mock_configure_ca,
                                                mock_cfg_canonical_names):
        mock_https.return_value = True
        mock_unit_get.return_value = '1.2.3.4'
        mock_determine_api_port.return_value = '12'
        mock_determine_apache_port.return_value = '34'
        mock_is_clustered.return_value = False

        ctxt = contexts.ApacheSSLContext()
        ctxt.enable_modules = MagicMock()
        ctxt.configure_cert = MagicMock()
        ctxt.configure_ca = MagicMock()
        ctxt.canonical_names = MagicMock()
        service_enabled.return_value = False
        self.assertEquals(ctxt(), {})
        self.assertFalse(mock_https.called)
        service_enabled.return_value = True
        self.assertEquals(ctxt(), {'endpoints': [('1.2.3.4', '1.2.3.4',
                                                  34, 12)],
                                   'ext_ports': [34],
                                   'namespace': 'cinder'})
        self.assertTrue(mock_https.called)
        mock_unit_get.assert_called_with('private-address')

    @patch('%s.relation_get' % (mod_ch_context))
    @patch('%s.related_units' % (mod_ch_context))
    @patch('%s.relation_ids' % (mod_ch_context))
    @patch('%s.log' % (mod_ch_context), lambda *args, **kwargs: None)
    def test_subordinate_config_context_stateless(self, mock_rel_ids,
                                                  mock_rel_units,
                                                  mock_rel_get):
        mock_rel_ids.return_value = ['storage-backend:0']
        self.relation_ids.return_value = ['storage-backend:0']

        mock_rel_units.return_value = ['cinder-ceph/0']
        self.related_units.return_value = ['cinder-ceph/0']

        self.service_name.return_value = 'cinder'

        settings = \
            {'backend_name': 'cinder-ceph',
             'private-address': '10.5.8.191',
             'stateless': 'True',
             'subordinate_configuration':
             '{"cinder": '
             '{"/etc/cinder/cinder.conf": '
             '{"sections": '
             '{"cinder-ceph": '
             '[["volume_backend_name", '
             '"cinder-ceph"], '
             '["volume_driver", '
             '"cinder.volume.drivers.rbd.RBDDriver"], '
             '["rbd_pool", '
             '"cinder-ceph"], '
             '["rbd_user", '
             '"cinder-ceph"]]}}}}'}

        def fake_rel_get(attribute=None, unit=None, rid=None):
            return settings.get(attribute)

        mock_rel_get.side_effect = fake_rel_get
        self.relation_get.side_effect = fake_rel_get

        ctxt = contexts.CinderSubordinateConfigContext(
            interface='storage-backend',
            service='cinder',
            config_file='/etc/cinder/cinder.conf')()

        exp = {'sections': {'DEFAULT': [('host', 'cinder')],
               u'cinder-ceph': [[u'volume_backend_name', u'cinder-ceph'],
                                [u'volume_driver',
                                 u'cinder.volume.drivers.rbd.RBDDriver'],
                                [u'rbd_pool', u'cinder-ceph'],
                                [u'rbd_user', u'cinder-ceph']]}}

        self.assertEquals(ctxt, exp)

    @patch('%s.relation_get' % (mod_ch_context))
    @patch('%s.related_units' % (mod_ch_context))
    @patch('%s.relation_ids' % (mod_ch_context))
    @patch('%s.log' % (mod_ch_context), lambda *args, **kwargs: None)
    def test_subordinate_config_context_statefull(self, mock_rel_ids,
                                                  mock_rel_units,
                                                  mock_rel_get):
        mock_rel_ids.return_value = ['storage-backend:0']
        self.relation_ids.return_value = ['storage-backend:0']

        mock_rel_units.return_value = ['cinder-ceph/0']
        self.related_units.return_value = ['cinder-ceph/0']

        self.service_name.return_value = 'cinder'

        settings = \
            {'backend_name': 'cinder-ceph',
             'private-address': '10.5.8.191',
             'stateless': 'False',
             'subordinate_configuration':
             '{"cinder": '
             '{"/etc/cinder/cinder.conf": '
             '{"sections": '
             '{"cinder-ceph": '
             '[["volume_backend_name", '
             '"cinder-ceph"], '
             '["volume_driver", '
             '"cinder.volume.drivers.rbd.RBDDriver"], '
             '["rbd_pool", '
             '"cinder-ceph"], '
             '["rbd_user", '
             '"cinder-ceph"]]}}}}'}

        def fake_rel_get(attribute=None, unit=None, rid=None):
            return settings.get(attribute)

        mock_rel_get.side_effect = fake_rel_get
        self.relation_get.side_effect = fake_rel_get

        ctxt = contexts.CinderSubordinateConfigContext(
            interface='storage-backend',
            service='cinder',
            config_file='/etc/cinder/cinder.conf')()

        exp = {'sections':
               {u'cinder-ceph': [[u'volume_backend_name',
                                  u'cinder-ceph'],
                                 [u'volume_driver',
                                  u'cinder.volume.drivers.rbd.RBDDriver'],
                                 [u'rbd_pool', u'cinder-ceph'],
                                 [u'rbd_user', u'cinder-ceph']]}}

        self.assertEquals(ctxt, exp)

        del settings['stateless']

        ctxt = contexts.CinderSubordinateConfigContext(
            interface='storage-backend',
            service='cinder',
            config_file='/etc/cinder/cinder.conf')()

        exp = {'sections':
               {u'cinder-ceph': [[u'volume_backend_name',
                                  u'cinder-ceph'],
                                 [u'volume_driver',
                                  u'cinder.volume.drivers.rbd.RBDDriver'],
                                 [u'rbd_pool', u'cinder-ceph'],
                                 [u'rbd_user', u'cinder-ceph']]}}

        self.assertEquals(ctxt, exp)

    @patch('%s.relation_get' % (mod_ch_context))
    @patch('%s.related_units' % (mod_ch_context))
    @patch('%s.relation_ids' % (mod_ch_context))
    @patch.object(contexts, 'log', lambda *args, **kwargs: None)
    @patch('%s.log' % (mod_ch_context), lambda *args, **kwargs: None)
    def test_subordinate_config_context_mixed(self, mock_rel_ids,
                                              mock_rel_units,
                                              mock_rel_get):
        mock_rel_ids.return_value = ['storage-backend:0', 'storage-backend:1']
        self.relation_ids.return_value = ['storage-backend:0',
                                          'storage-backend:1']

        def fake_rel_units(rid):
            if rid == 'storage-backend:0':
                return ['cinder-ceph/0']
            else:
                return ['cinder-other/0']

        mock_rel_units.side_effect = fake_rel_units
        self.related_units.side_effect = fake_rel_units

        self.service_name.return_value = 'cinder'

        cinder_ceph_settings = \
            {'backend_name': 'cinder-ceph',
             'private-address': '10.5.8.191',
             'stateless': 'True',
             'subordinate_configuration':
             '{"cinder": '
             '{"/etc/cinder/cinder.conf": '
             '{"sections": '
             '{"cinder-ceph": '
             '[["volume_backend_name", '
             '"cinder-ceph"], '
             '["volume_driver", '
             '"cinder.volume.drivers.rbd.RBDDriver"], '
             '["rbd_pool", '
             '"cinder-ceph"], '
             '["rbd_user", '
             '"cinder-ceph"]]}}}}'}

        cinder_other_settings = \
            {'backend_name': 'cinder-other',
             'private-address': '10.5.8.192',
             'subordinate_configuration':
             '{"cinder": '
             '{"/etc/cinder/cinder.conf": '
             '{"sections": '
             '{"cinder-other": '
             '[["volume_backend_name", '
             '"cinder-other"], '
             '["volume_driver", '
             '"cinder.volume.drivers.OtherDriver"]]}}}}'}

        def fake_rel_get(attribute=None, unit=None, rid=None):
            if unit == 'cinder-ceph/0':
                return cinder_ceph_settings.get(attribute)
            elif unit == 'cinder-other/0':
                return cinder_other_settings.get(attribute)

        mock_rel_get.side_effect = fake_rel_get
        self.relation_get.side_effect = fake_rel_get

        ctxt = contexts.CinderSubordinateConfigContext(
            interface='storage-backend',
            service='cinder',
            config_file='/etc/cinder/cinder.conf')()

        exp = {'sections':
               {u'cinder-ceph': [[u'volume_backend_name',
                                  u'cinder-ceph'],
                                 [u'volume_driver',
                                  u'cinder.volume.drivers.rbd.RBDDriver'],
                                 [u'rbd_pool', u'cinder-ceph'],
                                 [u'rbd_user', u'cinder-ceph']],
                u'cinder-other': [[u'volume_backend_name',
                                   u'cinder-other'],
                                  [u'volume_driver',
                                   u'cinder.volume.drivers.OtherDriver']]}}

        self.assertEquals(ctxt, exp)

    def test_region_context(self):
        self.config.return_value = 'two'
        ctxt = contexts.RegionContext()()
        self.assertEqual('two', ctxt['region'])

    @patch('__builtin__.open')
    def test_volume_usage_audit_context(self, _open):
        self.config.return_value = 'month'
        ctxt = contexts.VolumeUsageAuditContext()()
        _open.assert_called_with(
            contexts.VolumeUsageAuditContext.DEFAULT_CRONTAB_PATH, "w+")
        self.assertEqual(self.config.return_value,
                         ctxt["volume_usage_audit_period"])
