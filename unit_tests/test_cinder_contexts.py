import cinder_contexts as contexts
import os

os.environ['JUJU_UNIT_NAME'] = 'cinder'
import cinder_utils as utils

from mock import patch

from test_utils import (
    CharmTestCase
)

TO_PATCH = [
    'config',
    'relation_ids',
    'service_name',
    'determine_apache_port',
    'determine_api_port',
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
        service = 'mycinder'
        self.service_name.return_value = service
        self.assertEquals(
            contexts.CephContext()(),
            {'volume_driver': 'cinder.volume.driver.RBDDriver',
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

    @patch('charmhelpers.contrib.openstack.context.determine_apache_port')
    @patch('charmhelpers.contrib.openstack.context.determine_api_port')
    @patch('charmhelpers.contrib.openstack.context.unit_get')
    @patch('charmhelpers.contrib.openstack.context.https')
    @patch.object(utils, 'service_enabled')
    def test_apache_ssl_context_service_enabled(self, service_enabled,
                                                mock_https, mock_unit_get,
                                                mock_determine_api_port,
                                                mock_determine_apache_port):
        mock_https.return_value = True
        mock_unit_get.return_value = '1.2.3.4'
        mock_determine_api_port.return_value = '12'
        mock_determine_apache_port.return_value = '34'

        ctxt = contexts.ApacheSSLContext()
        with patch.object(ctxt, 'enable_modules') as mock_enable_modules:
            with patch.object(ctxt, 'configure_cert') as mock_configure_cert:
                service_enabled.return_value = False
                self.assertEquals(ctxt(), {})
                self.assertFalse(mock_https.called)

                service_enabled.return_value = True
                self.assertEquals(ctxt(), {'endpoints': [(34, 12)],
                                           'private_address': '1.2.3.4',
                                           'namespace': 'cinder'})
                self.assertTrue(mock_https.called)
                mock_unit_get.assert_called_with('private-address')
