from mock import patch
import cinder_contexts as contexts
import cinder_utils as utils

from test_utils import (
    CharmTestCase
)

TO_PATCH = [
    'config',
    'relation_ids',
    'service_name',
    'determine_haproxy_port',
    'determine_api_port',
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

    def test_haproxy_configuration(self):
        self.determine_haproxy_port.return_value = 8080
        self.determine_api_port.return_value = 8090
        self.assertEquals(
            contexts.HAProxyContext()(),
            {'service_ports': {'cinder_api': [8080, 8090]},
             'osapi_volume_listen_port': 8090})

    @patch.object(utils, 'service_enabled')
    def test_apache_ssl_context_service_disabled(self, service_enabled):
        service_enabled.return_value = False
        self.assertEquals(contexts.ApacheSSLContext()(), {})

    @patch('charmhelpers.contrib.openstack.context.https')
    @patch.object(utils, 'service_enabled')
    def test_apache_ssl_context_service_enabled(self, service_enabled,
                                                https):
        service_enabled.return_value = True
        https.return_value = False
        self.assertEquals(contexts.ApacheSSLContext()(), {})
