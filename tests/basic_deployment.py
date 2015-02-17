#!/usr/bin/python

import amulet
import types
from time import sleep
import cinderclient.v1.client as cinder_client

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (  # noqa
    OpenStackAmuletUtils,
    DEBUG,
    ERROR
)

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(DEBUG)


class CinderBasicDeployment(OpenStackAmuletDeployment):
    '''Amulet tests on a basic lvm-backed cinder deployment. Verify
       relations, service status, users and endpoint service catalog.
       Create, clone, delete volumes. Create volume from glance image.
       Create volume snapshot. Create volume from snapshot.'''

    # NOTE(beisner):  Features and tests vary across Openstack releases.
    # https://wiki.openstack.org/wiki/CinderSupportMatrix

    def __init__(self, series=None, openstack=None, source=None, stable=False):
        '''Deploy the entire test environment.'''
        super(CinderBasicDeployment, self).__init__(series, openstack, source,
                                                    stable)
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()
        self._initialize_tests()

    def _add_services(self):
        """Add services

           Add the services that we're testing, where cinder is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {'name': 'cinder'}
        other_services = [{'name': 'mysql'}, {'name': 'rabbitmq-server'},
                          {'name': 'keystone'}, {'name': 'glance'}]
        super(CinderBasicDeployment, self)._add_services(this_service,
                                                         other_services)

    def _add_relations(self):
        '''Add relations for the services.'''
        relations = {
            'keystone:shared-db': 'mysql:shared-db',
            'cinder:shared-db': 'mysql:shared-db',
            'cinder:identity-service': 'keystone:identity-service',
            'cinder:amqp': 'rabbitmq-server:amqp',
            'cinder:image-service': 'glance:image-service',
            'glance:identity-service': 'keystone:identity-service',
            'glance:shared-db': 'mysql:shared-db',
            'glance:amqp': 'rabbitmq-server:amqp'
        }
        super(CinderBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        '''Configure all of the services.'''
        keystone_config = {'admin-password': 'openstack',
                           'admin-token': 'ubuntutesting'}
        cinder_config = {'block-device': 'vdb',
                         'glance-api-version': '2',
                         'overwrite': 'true'}
        mysql_config = {'dataset-size': '50%'}
        configs = {'cinder': cinder_config,
                   'keystone': keystone_config,
                   'mysql': mysql_config}
        super(CinderBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        '''Perform final initialization before tests get run.'''
        # Access the sentries for inspecting service units
        self.cinder_sentry = self.d.sentry.unit['cinder/0']
        self.glance_sentry = self.d.sentry.unit['glance/0']
        self.mysql_sentry = self.d.sentry.unit['mysql/0']
        self.keystone_sentry = self.d.sentry.unit['keystone/0']
        self.rabbitmq_sentry = self.d.sentry.unit['rabbitmq-server/0']

        # Authenticate admin with keystone
        self.keystone = u.authenticate_keystone_admin(self.keystone_sentry,
                                                      user='admin',
                                                      password='openstack',
                                                      tenant='admin')
        # Authenticate admin with cinder endpoint
        self.cinder = self.authenticate_cinder_admin(username='admin',
                                                     password='openstack',
                                                     tenant='admin')
        # Authenticate admin with glance endpoint
        self.glance = u.authenticate_glance_admin(self.keystone)

        u.log.debug('openstack rel: {}'.format(self._get_openstack_release()))
        # Wait for relations to settle
        sleep(120)

    def service_restarted(self, sentry_unit, service, filename,
                          pgrep_full=False, sleep_time=60):
        """Compare a service's start time vs a file's last modification time
           (such as a config file for that service) to determine if the service
           has been restarted (within 60s by default), return when verified."""

        # NOTE(beisner): prev rev utilized sleep_time as an arbitrary wait with
        # no debug feedback.  Added checking timeout loop logic & debug output.
        # Increased default timeout to 60s due to test failures.

        # NOTE(beisner): need to move to charmhelpers, and adjust calls here.
        # It is backward compatible with prev rev by coreycb.

        proc_start_time = u._get_proc_start_time(sentry_unit,
                                                 service, pgrep_full)
        file_mtime = u._get_file_mtime(sentry_unit, filename)

        tries = 0
        while proc_start_time < file_mtime and tries < (sleep_time/4):
            sleep(4)
            proc_start_time = u._get_proc_start_time(sentry_unit,
                                                     service, pgrep_full)
            file_mtime = u._get_file_mtime(sentry_unit, filename)
            u.log.debug('proc restart wait: {} {}'.format(tries, service))
            tries += 1

        u.log.debug('proc-file time diff for {},{}: {}'.format(service,
                    filename, proc_start_time - file_mtime))

        if proc_start_time >= file_mtime:
            return True
        else:
            u.log.debug('service not restarted within ()s: {}'.format(
                service, sleep_time))
            return False

    def authenticate_cinder_admin(self, username, password, tenant):
        """Authenticates admin user with cinder."""
        # NOTE(beisner): need to move to charmhelpers, and adjust calls here.
        # Probably useful on other charm tests.
        service_ip = \
            self.keystone_sentry.relation('shared-db',
                                          'mysql:shared-db')['private-address']
        ept = "http://{}:5000/v2.0".format(service_ip.strip().decode('utf-8'))
        return cinder_client.Client(username, password, tenant, ept)

    def force_list(self, obj):
        '''Determine the object type and return a list.  Some Openstack
           component API list methods return generators, some return lists.
           Where obj is cinder.volumes, cinder.volume_snapshots, glance.images,
           or other Openstack object with a list method.'''
        # NOTE(beisner): need to move to charmhelpers, and adjust calls here.

        # NOTE(beisner):  Beware - glance's list method returns a generator,
        # and cinder's list method returns a list!
        if isinstance(obj.list(), types.ListType):
            return obj.list()
        elif isinstance(obj.list(), types.GeneratorType):
            return list(obj.list())
        else:
            u.log.debug('unhandled object type: {}'.format(type(obj.list())))
            return False

    def delete_all_objs(self, obj, item_desc='object', max_wait=60):
        '''Delete all objects from openstack component, such as all volumes,
           all images or all snapshots.  Waits and confirms deletion.'''
        # NOTE(beisner): need to move to charmhelpers, and adjust calls here.
        # Probably useful on other charm tests.

        # Get list of objects to delete
        obj_list = self.force_list(obj)
        if obj_list is False:
            return '{} list failed'.format(item_desc)

        if len(obj_list) == 0:
            u.log.debug('no {}(s) to delete'.format(item_desc))
            return None

        # Delete objects
        for obj_this in obj_list:
            u.log.debug('deleting {}: {}'.format(item_desc, obj_this.id))
            try:
                obj_this.delete()
            except:
                return '{} delete failed for {} with status {}'.format(
                       item_desc, obj_this.id, obj_this.status)

        # Wait for objects to disappear
        obj_count = len(self.force_list(obj))
        tries = 0
        while obj_count != 0 and tries <= (max_wait/4):
            u.log.debug('{} delete wait: {} {}'.format(item_desc,
                                                       tries, obj_count))
            sleep(4)
            obj_count = len(self.force_list(obj))
            tries += 1

        if obj_count != 0:
            return '{}(s) not deleted, {} remain.'.format(item_desc,
                                                          obj_count)

    def obj_is_status(self, obj, obj_id, stat='available',
                      msg='openstack object status check', max_wait=60):
        ''''Wait for an openstack object status to be as expected.
            By default, expect an available status within 60s.  Useful
            when confirming cinder volumes, snapshots, glance images, etc.
            reach a certain state/status within a specified time.'''
        # NOTE(beisner): need to move to charmhelpers, and adjust calls here.
        # Probably useful on other charm tests.

        obj_stat = obj.get(obj_id).status
        tries = 0
        while obj_stat != stat and tries < (max_wait/4):
            u.log.debug(msg + ': {} [{}:{}] {}'.format(tries, obj_stat,
                                                       stat, obj_id))
            sleep(4)
            obj_stat = obj.get(obj_id).status
            tries += 1
        if obj_stat == stat:
            return True
        else:
            return False

    def test_services(self):
        '''Verify that the expected services are running on the
           corresponding service units.'''
        commands = {
            self.cinder_sentry: ['status cinder-api',
                                 'status cinder-scheduler',
                                 'status cinder-volume'],
            self.glance_sentry: ['status glance-registry',
                                 'status glance-api'],
            self.mysql_sentry: ['status mysql'],
            self.keystone_sentry: ['status keystone'],
            self.rabbitmq_sentry: ['sudo service rabbitmq-server status']
        }
        u.log.debug('commands: {}'.format(commands))
        ret = u.validate_services(commands)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_service_catalog(self):
        '''Verify that the service catalog endpoint data'''
        endpoint_vol = {'adminURL': u.valid_url,
                        'region': 'RegionOne',
                        'publicURL': u.valid_url,
                        'internalURL': u.valid_url}
        endpoint_id = {'adminURL': u.valid_url,
                       'region': 'RegionOne',
                       'publicURL': u.valid_url,
                       'internalURL': u.valid_url}
        if self._get_openstack_release() >= self.trusty_icehouse:
            endpoint_vol['id'] = u.not_null
            endpoint_id['id'] = u.not_null

        expected = {'image': [endpoint_id],
                    'identity': [endpoint_id],
                    'volume': [endpoint_id]}
        actual = self.keystone.service_catalog.get_endpoints()

        ret = u.validate_svc_catalog_endpoint_data(expected, actual)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_cinder_glance_image_service_relation(self):
        '''Verify the cinder:glance image-service relation data'''
        unit = self.cinder_sentry
        relation = ['image-service', 'glance:image-service']
        expected = {'private-address': u.valid_ip}
        u.log.debug('')
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('cinder image-service', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_glance_cinder_image_service_relation(self):
        '''Verify the glance:cinder image-service relation data'''
        unit = self.glance_sentry
        relation = ['image-service', 'cinder:image-service']
        expected = {
            'private-address': u.valid_ip,
            'glance-api-server': u.valid_url
        }
        u.log.debug('')
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('glance image-service', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_mysql_cinder_db_relation(self):
        '''Verify the mysql:glance shared-db relation data'''
        unit = self.mysql_sentry
        relation = ['shared-db', 'cinder:shared-db']
        expected = {
            'private-address': u.valid_ip,
            'db_host': u.valid_ip
        }
        u.log.debug('')
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('mysql shared-db', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_cinder_mysql_db_relation(self):
        '''Verify the cinder:mysql shared-db relation data'''
        unit = self.cinder_sentry
        relation = ['shared-db', 'mysql:shared-db']
        expected = {
            'private-address': u.valid_ip,
            'hostname': u.valid_ip,
            'username': 'cinder',
            'database': 'cinder'
        }
        u.log.debug('')
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('cinder shared-db', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_keystone_cinder_id_relation(self):
        '''Verify the keystone:cinder identity-service relation data'''
        unit = self.keystone_sentry
        relation = ['identity-service',
                    'cinder:identity-service']
        expected = {
            'service_protocol': 'http',
            'service_tenant': 'services',
            'admin_token': 'ubuntutesting',
            'service_password': u.not_null,
            'service_port': '5000',
            'auth_port': '35357',
            'auth_protocol': 'http',
            'private-address': u.valid_ip,
            'https_keystone': 'False',
            'auth_host': u.valid_ip,
            'service_username': 'cinder',
            'service_tenant_id': u.not_null,
            'service_host': u.valid_ip
        }
        u.log.debug('')
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('identity-service cinder', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_cinder_keystone_id_relation(self):
        '''Verify the cinder:keystone identity-service relation data'''
        unit = self.cinder_sentry
        relation = ['identity-service',
                    'keystone:identity-service']
        expected = {
            'service': 'cinder',
            'region': 'RegionOne',
            'public_url': u.valid_url,
            'internal_url': u.valid_url,
            'admin_url': u.valid_url,
            'private-address': u.valid_ip
        }
        u.log.debug('')
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('cinder identity-service', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_rabbitmq_cinder_amqp_relation(self):
        '''Verify the rabbitmq-server:cinder amqp relation data'''
        unit = self.rabbitmq_sentry
        relation = ['amqp', 'cinder:amqp']
        expected = {
            'private-address': u.valid_ip,
            'password': u.not_null,
            'hostname': u.valid_ip
        }
        u.log.debug('')
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('amqp cinder', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_cinder_rabbitmq_amqp_relation(self):
        '''Verify the cinder:rabbitmq-server amqp relation data'''
        unit = self.cinder_sentry
        relation = ['amqp', 'rabbitmq-server:amqp']
        expected = {
            'private-address': u.valid_ip,
            'vhost': 'openstack',
            'username': u.not_null
        }
        u.log.debug('')
        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            msg = u.relation_error('cinder amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_cinder_default_config(self):
        '''Verify default section configs in cinder.conf and
           compare some of the parameters to relation data.'''
        unit_ci = self.cinder_sentry
        unit_mq = self.rabbitmq_sentry
        rel_ci_mq = unit_ci.relation('amqp', 'rabbitmq-server:amqp')
        rel_mq_ci = unit_mq.relation('amqp', 'cinder:amqp')
        u.log.debug('actual ci:mq relation: {}'.format(rel_ci_mq))
        u.log.debug('actual mq:ci relation: {}'.format(rel_mq_ci))
        conf = '/etc/cinder/cinder.conf'
        expected = {'use_syslog': 'False',
                    'debug': 'False',
                    'verbose': 'False',
                    'iscsi_helper': 'tgtadm',
                    'volume_group': 'cinder-volumes',
                    'rabbit_userid': 'cinder',
                    'rabbit_password': rel_mq_ci['password'],
                    'rabbit_host': rel_mq_ci['hostname'],
                    'auth_strategy': 'keystone',
                    'volumes_dir': '/var/lib/cinder/volumes'}
        section = 'DEFAULT'
        u.log.debug('')
        ret = u.validate_config_data(unit_ci, conf, section, expected)
        if ret:
            msg = 'cinder.conf default config error: {}'.format(ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_cinder_auth_config(self):
        '''Verify authtoken section config in cinder.conf or
           api-paste.ini using glance/keystone relation data.'''
        unit_ci = self.cinder_sentry
        unit_ks = self.keystone_sentry
        rel_ks_ci = unit_ks.relation('identity-service',
                                     'cinder:identity-service')
        u.log.debug('actual ks:ci relation: {}'.format(rel_ks_ci))

        expected = {'admin_user': rel_ks_ci['service_username'],
                    'admin_password': rel_ks_ci['service_password'],
                    'admin_tenant_name': rel_ks_ci['service_tenant'],
                    'auth_host': rel_ks_ci['auth_host']}

        if self._get_openstack_release() >= self.precise_icehouse:
            conf = '/etc/cinder/cinder.conf'
            section = 'keystone_authtoken'
            auth_uri = 'http://' + rel_ks_ci['auth_host'] + \
                       ':' + rel_ks_ci['service_port'] + '/'
            expected['auth_uri'] = auth_uri
        else:
            conf = '/etc/cinder/api-paste.ini'
            section = 'filter:authtoken'

        ret = u.validate_config_data(unit_ci, conf, section, expected)
        if ret:
            msg = "cinder auth config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_cinder_logging_config(self):
        ''' Inspect select sections and config pairs in logging.conf.'''
        unit_ci = self.cinder_sentry
        conf = '/etc/cinder/logging.conf'

        expected = {
            'loggers': {
                'keys': 'root, cinder'
            },
            'logger_cinder': {
                'level': 'INFO',
                'handlers': 'stderr',
                'qualname': 'cinder'
            },
            'logger_root': {
                'level': 'WARNING',
                'handlers': 'null'
            }
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit_ci, conf, section, pairs)
            if ret:
                msg = "cinder logging config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=msg)

    def test_cinder_rootwrap_config(self):
        ''' Inspect select config pairs in rootwrap.conf. '''
        unit_ci = self.cinder_sentry
        conf = '/etc/cinder/rootwrap.conf'
        expected = {'filters_path': '/etc/cinder/rootwrap.d,'
                                    '/usr/share/cinder/rootwrap'}
        section = 'DEFAULT'

        if self._get_openstack_release() >= self.precise_havana:
            expected['use_syslog'] = 'False'
            expected['exec_dirs'] = '/sbin,/usr/sbin,/bin,/usr/bin'

        ret = u.validate_config_data(unit_ci, conf, section, expected)
        if ret:
            msg = "cinder rootwrap config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_cinder_endpoint(self):
        '''Verify the cinder endpoint data.'''
        endpoints = self.keystone.endpoints.list()
        admin_port = internal_port = public_port = '8776'
        expected = {'id': u.not_null,
                    'region': 'RegionOne',
                    'adminurl': u.valid_url,
                    'internalurl': u.valid_url,
                    'publicurl': u.valid_url,
                    'service_id': u.not_null}

        ret = u.validate_endpoint_data(endpoints, admin_port, internal_port,
                                       public_port, expected)
        if ret:
            amulet.raise_status(amulet.FAIL,
                                msg='glance endpoint: {}'.format(ret))

    def test_z_cinder_restart_on_config_change(self):
        '''Verify cinder services are restarted when the config is changed.

           Note(coreycb): The method name with the _z_ is a little odd
           but it forces the test to run last.  It just makes things
           easier because restarting services requires re-authorization.
           '''
        u.log.debug('making charm config change')
        self.d.configure('cinder', {'verbose': 'True'})
        self.d.configure('cinder', {'debug': 'True'})

        if not self.service_restarted(self.cinder_sentry, 'cinder-api',
                                      '/etc/cinder/cinder.conf',
                                      sleep_time=90):
            self.d.configure('cinder', {'verbose': 'False'})
            self.d.configure('cinder', {'debug': 'False'})
            msg = "cinder-api service didn't restart after config change"
            amulet.raise_status(amulet.FAIL, msg=msg)

        if not self.service_restarted(self.cinder_sentry, 'cinder-volume',
                                      '/etc/cinder/cinder.conf', sleep_time=0):
            self.d.configure('cinder', {'verbose': 'False'})
            self.d.configure('cinder', {'debug': 'False'})
            msg = "cinder-volume service didn't restart after conf change"
            amulet.raise_status(amulet.FAIL, msg=msg)

        u.log.debug('returning to original charm config')
        self.d.configure('cinder', {'verbose': 'False'})
        self.d.configure('cinder', {'debug': 'False'})

    def test_users(self):
        '''Verify expected users.'''
        user0 = {'name': 'cinder',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': 'juju@localhost'}
        user1 = {'name': 'admin',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': 'juju@localhost'}
        user2 = {'name': 'glance',
                 'enabled': True,
                 'tenantId': u.not_null,
                 'id': u.not_null,
                 'email': 'juju@localhost'}
        expected = [user0, user1, user2]
        actual = self.keystone.users.list()

        ret = u.validate_user_data(expected, actual)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_000_delete_volumes_snapshots_images(self):
        '''Delete all volumes, snapshots and images, if they exist,
        as the first of the ordered tests.  Useful in re-run scenarios.'''
        self.test_900_delete_all_snapshots()
        self.test_900_glance_delete_all_images()
        self.test_999_delete_all_volumes()

    def test_100_create_and_extend_volume(self):
        '''Add and confirm a new 1GB volume. In Havana and later,
           extend that volume to 2GB.'''
        # Create new volume
        vol_new = self.cinder.volumes.create(display_name="demo-vol", size=1)
        vol_id = vol_new.id

        # Wait for volume status to be available
        ret = self.obj_is_status(self.cinder.volumes, obj_id=vol_id,
                                 stat='available',
                                 msg='create vol status wait')
        if not ret:
            msg = 'volume create failed'
            amulet.raise_status(amulet.FAIL, msg=msg)

        # NOTE(beisner): Cinder extend is supported only in Havana or later
        if self._get_openstack_release() < self.precise_havana:
            u.log.debug('Skipping volume extend due to openstack release < H')
            return

        # Extend volume size
        self.cinder.volumes.extend(vol_id, '2')

        # Wait for extend
        vol_size = self.cinder.volumes.get(vol_id).size
        tries = 0
        while vol_size != 2 and tries <= 15:
            u.log.debug('volume extend size wait: {} {}'.format(tries,
                                                                vol_id))
            sleep(4)
            vol_size = self.cinder.volumes.get(vol_id).size
            tries += 1

        if vol_size != 2:
            msg = 'Failed to extend volume, size is {}'.format(vol_size)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_100_glance_image_create(self):
        '''Create new cirros glance image, to be referenced by
           a cinder volume create tests in Havana or later.'''

        # NOTE(beisner): Cinder create vol-from-img support for lvm and
        # rbd(ceph) exists only in Havana or later
        if self._get_openstack_release() < self.precise_havana:
            u.log.debug('Skipping create glance img due to openstack rel < H')
            return

        # Create a new image
        image_new = u.create_cirros_image(self.glance, 'cirros-image-1')

        # Confirm image is created and has status of 'active'
        if not image_new:
            msg = 'image create failed'
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_200_clone_volume(self):
        '''Create a new cinder volume, clone it to another cinder volume.'''
        # Get volume object and ID
        try:
            vol = self.cinder.volumes.find(display_name="demo-vol")
            vol_id = vol.id
            vol_size = vol.size
        except:
            msg = ('Volume (demo-vol) not found.')
            amulet.raise_status(amulet.FAIL, msg=msg)

        if vol.status != 'available':
            msg = ('volume status not == available: {}'.format(vol.status))
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Create new clone volume from source volume
        vol_clone = self.cinder.volumes.create(display_name="demo-vol-clone",
                                               size=vol_size,
                                               source_volid=vol_id)

        ret = self.obj_is_status(self.cinder.volumes, obj_id=vol_clone.id,
                                 stat='available',
                                 msg='clone vol status wait')
        if not ret:
            msg = 'volume clone failed - from {}'.format(vol_id)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_200_create_volume_from_glance_image(self):
        '''Create new volume from glance cirros image (Havana and later),
           check status and bootable flag.'''

        # NOTE(beisner): Cinder create vol-from-img support for lvm and
        # rbd(ceph) exists only in Havana or later
        if self._get_openstack_release() < self.precise_havana:
            u.log.debug('Skipping create vol from img, openstack rel < H')
            return

        # Get image object and id
        expected_img_name = 'cirros-image-1'
        img_list = list(self.glance.images.list())
        img_count = len(img_list)

        if img_count != 0:
            # NOTE(beisner): glance api has no find method, presume 1st image
            img_id = img_list[0].id
        else:
            msg = 'image not found'
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Confirm image name
        if img_list[0].name != expected_img_name:
            msg = 'unexpected image name {}'.format(img_list[0].name)
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Create new volume from glance image
        vol_new = self.cinder.volumes.create(display_name="demo-vol-cirros",
                                             size=1, imageRef=img_id)
        vol_id = vol_new.id

        # Wait for volume stat to be avail, check that it's flagged bootable
        ret = self.obj_is_status(self.cinder.volumes, obj_id=vol_id,
                                 stat='available',
                                 msg='create vol from img status wait')
        vol_boot = self.cinder.volumes.get(vol_id).bootable

        if not ret or vol_boot != 'true':
            vol_stat = self.cinder.volumes.get(vol_id).status
            msg = ('vol create failed - from glance img:'
                   ' id:{} stat:{} boot:{}'.format(vol_id,
                                                   vol_stat,
                                                   vol_boot))
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_300_cinder_create_snapshot(self):
        '''Create a snapshot of a volume. Use a cirros-based volume where
           supported (Havana and newer), and fall back to a vanilla
           volume snapshot everywhere else.'''

        if self._get_openstack_release() >= self.precise_havana:
            vol_src_name = "demo-vol-cirros"
        elif self._get_openstack_release() < self.precise_havana:
            vol_src_name = "demo-vol"

        u.log.debug('creating snapshot of volume: {}'.format(vol_src_name))

        # Get volume object and id
        try:
            vol_src = self.cinder.volumes.find(display_name=vol_src_name)
            vol_id = vol_src.id
        except:
            msg = ('volume not found while creating snapshot')
            amulet.raise_status(amulet.FAIL, msg=msg)

        if vol_src.status != 'available':
            msg = ('volume status not == available: {}').format(vol_src.status)
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Create new snapshot
        snap_new = self.cinder.volume_snapshots.create(
            volume_id=vol_id, display_name='demo-snapshot')
        snap_id = snap_new.id

        # Wait for snapshot status to become available
        ret = self.obj_is_status(self.cinder.volume_snapshots, obj_id=snap_id,
                                 stat='available',
                                 msg='snapshot create status wait')
        if not ret:
            snap_stat = self.cinder.volume_snapshots.get(snap_id).status
            msg = 'volume snapshot failed: {} {}'.format(snap_id,
                                                         snap_stat)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_310_create_volume_from_snapshot(self):
        '''Create a new volume from a snapshot of a volume.'''
        # Get snapshot object and ID
        try:
            snap = self.cinder.volume_snapshots.find(
                display_name="demo-snapshot")
            snap_id = snap.id
            snap_size = snap.size
        except:
            msg = 'snapshot not found while creating volume'
            amulet.raise_status(amulet.FAIL, msg=msg)

        if snap.status != 'available':
            msg = 'snapshot status not == available: {}'.format(snap.status)
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Create new volume from snapshot
        vol_new = self.cinder.volumes.create(
            display_name="demo-vol-from-snap",
            snapshot_id=snap_id,
            size=snap_size)
        vol_id = vol_new.id

        # Wait for volume status to be == available
        ret = self.obj_is_status(self.cinder.volumes, obj_id=vol_id,
                                 stat='available',
                                 msg='vol from snap create status wait')
        if not ret:
            vol_stat = self.cinder.volumes.get(vol_id).status
            msg = 'volume create failed: {} {}'.format(vol_id,
                                                       vol_stat)
            amulet.raise_status(amulet.FAIL, msg=msg)

    def test_900_confirm_lvm_volume_list(self):
        '''Confirm cinder volume IDs with lvm logical volume IDs.
           Expect a 1:1 relationship of lvm:cinder volumes.'''
        commando = self.cinder_sentry.run('sudo lvs | grep cinder-volumes | '
                                          'awk \'{ print $1 }\'')
        vol_list = self.cinder.volumes.list()
        lv_id_list = commando[0].split('\n')
        vol_count = len(vol_list)
        snap_count = len(self.cinder.volume_snapshots.list())

        # Expect cinder vol + snap count to match lvm log vol count
        if (vol_count + snap_count) != len(lv_id_list):
            msg = ('lvm volume count ({}) != cinder volume + snap count '
                   '({})'.format(len(vol_list), len(lv_id_list)))
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Expect all cinder vol IDs to exist in the LVM volume list
        for vol_this in vol_list:
            try:
                lv_id_list.index('volume-' + vol_this.id)
            except:
                msg = ('volume ID {} not found in '
                       'LVM volume list.'.format(vol_this.id))
                amulet.raise_status(amulet.FAIL, msg=msg)

    def test_900_glance_delete_all_images(self):
        '''Delete all glance images and confirm deletion.'''
        ret = self.delete_all_objs(self.glance.images, item_desc='image')
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_900_delete_all_snapshots(self):
        '''Delete all cinder volume snapshots and confirm deletion.'''
        ret = self.delete_all_objs(self.cinder.volume_snapshots,
                                   item_desc='snapshot')
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_999_delete_all_volumes(self):
        '''Delete all cinder volumes and confirm deletion,
           as the last of the ordered tests.'''
        ret = self.delete_all_objs(self.cinder.volumes, item_desc='volume')
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)
