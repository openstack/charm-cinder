#!/usr/bin/python
#
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
import uuid

from subprocess import (
    check_call,
)

from cinder_utils import (
    determine_packages,
    disable_package_apache_site,
    do_openstack_upgrade,
    juju_log,
    migrate_database,
    configure_lvm_storage,
    register_configs,
    restart_map,
    run_in_apache,
    services,
    service_enabled,
    service_restart,
    set_ceph_env_variables,
    CLUSTER_RES,
    CINDER_CONF,
    CINDER_API_CONF,
    ceph_config_file,
    setup_ipv6,
    check_local_db_actions_complete,
    filesystem_mounted,
    assess_status,
)

from charmhelpers.core.hookenv import (
    Hooks,
    UnregisteredHookError,
    config,
    local_unit,
    relation_get,
    relation_ids,
    relation_set,
    related_units,
    service_name,
    log,
    DEBUG,
    WARNING,
    status_set,
    open_port,
)

from charmhelpers.fetch import (
    apt_install,
    apt_update
)

from charmhelpers.core.host import (
    lsb_release,
    service_reload,
    umount,
)

from charmhelpers.contrib.openstack.utils import (
    config_value_changed,
    configure_installation_source,
    openstack_upgrade_available,
    sync_db_with_multi_ipv6_addresses,
    os_release,
    is_unit_paused_set,
    pausable_restart_on_change as restart_on_change,
    CompareOpenStackReleases,
)

from charmhelpers.contrib.storage.linux.ceph import (
    send_request_if_needed,
    is_request_complete,
    ensure_ceph_keyring,
    CephBrokerRq,
    delete_keyring,
)

from charmhelpers.contrib.hahelpers.cluster import (
    is_clustered,
    is_elected_leader,
    get_hacluster_config,
)

from charmhelpers.contrib.openstack.ha.utils import (
    update_dns_ha_resource_params,
)

from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.contrib.network.ip import (
    get_iface_for_address,
    get_netmask_for_address,
    is_ipv6,
    get_relation_ip,
)
from charmhelpers.contrib.openstack.ip import (
    canonical_url,
    PUBLIC, INTERNAL, ADMIN
)
from charmhelpers.contrib.openstack.context import ADDRESS_TYPES

from charmhelpers.contrib.charmsupport import nrpe
from charmhelpers.contrib.hardening.harden import harden

hooks = Hooks()

CONFIGS = register_configs()


@hooks.hook('install.real')
@harden()
def install():
    status_set('maintenance', 'Executing pre-install')
    execd_preinstall()
    conf = config()
    src = conf['openstack-origin']
    if (lsb_release()['DISTRIB_CODENAME'] == 'precise' and
            src == 'distro'):
        src = 'cloud:precise-folsom'
    configure_installation_source(src)

    status_set('maintenance', 'Installing apt packages')
    apt_update()
    apt_install(determine_packages(), fatal=True)

    if run_in_apache():
        disable_package_apache_site()


@hooks.hook('config-changed')
@restart_on_change(restart_map(), stopstart=True)
@harden()
def config_changed():
    conf = config()

    if conf['prefer-ipv6']:
        status_set('maintenance', 'configuring ipv6')
        setup_ipv6()
        sync_db_with_multi_ipv6_addresses(config('database'),
                                          config('database-user'))

    e_mountpoint = config('ephemeral-unmount')
    if e_mountpoint and filesystem_mounted(e_mountpoint):
        umount(e_mountpoint)

    if (service_enabled('volume') and
            conf['block-device'] not in [None, 'None', 'none']):
        status_set('maintenance', 'Configuring lvm storage')
        block_devices = conf['block-device'].split()
        configure_lvm_storage(block_devices,
                              conf['volume-group'],
                              conf['overwrite'] in ['true', 'True', True],
                              conf['remove-missing'],
                              conf['remove-missing-force'])

    if not config('action-managed-upgrade'):
        if openstack_upgrade_available('cinder-common'):
            status_set('maintenance', 'Running openstack upgrade')
            do_openstack_upgrade(configs=CONFIGS)
            # NOTE(jamespage) tell any storage-backends we just upgraded
            for rid in relation_ids('storage-backend'):
                relation_set(relation_id=rid,
                             upgrade_nonce=uuid.uuid4())
            # NOTE(hopem) tell any backup-backends we just upgraded
            for rid in relation_ids('backup-backend'):
                relation_set(relation_id=rid,
                             upgrade_nonce=uuid.uuid4())

    # overwrite config is not in conf file. so We can't use restart_on_change
    if config_value_changed('overwrite') and not is_unit_paused_set():
        service_restart('cinder-volume')

    CONFIGS.write_all()
    configure_https()
    update_nrpe_config()
    open_port(config('api-listening-port'))

    for rid in relation_ids('cluster'):
        cluster_joined(relation_id=rid)
    for r_id in relation_ids('ha'):
        ha_joined(relation_id=r_id)


@hooks.hook('shared-db-relation-joined')
def db_joined():
    if config('prefer-ipv6'):
        sync_db_with_multi_ipv6_addresses(config('database'),
                                          config('database-user'))
    else:
        # Avoid churn check for access-network early
        access_network = None
        for unit in related_units():
            access_network = relation_get(unit=unit,
                                          attribute='access-network')
            if access_network:
                break
        host = get_relation_ip('shared-db', cidr_network=access_network)

        conf = config()
        relation_set(database=conf['database'],
                     username=conf['database-user'],
                     hostname=host)


@hooks.hook('shared-db-relation-changed')
@restart_on_change(restart_map())
def db_changed():
    if 'shared-db' not in CONFIGS.complete_contexts():
        juju_log('shared-db relation incomplete. Peer not ready?')
        return
    CONFIGS.write(CINDER_CONF)
    if is_elected_leader(CLUSTER_RES):
        # Bugs 1353135 & 1187508. Dbs can appear to be ready before the units
        # acl entry has been added. So, if the db supports passing a list of
        # permitted units then check if we're in the list.
        allowed_units = relation_get('allowed_units')
        if allowed_units and local_unit() in allowed_units.split():
            juju_log('Cluster leader, performing db sync')
            migrate_database()
        else:
            juju_log('allowed_units either not presented, or local unit '
                     'not in acl list: %s' % repr(allowed_units))


@hooks.hook('amqp-relation-joined')
def amqp_joined(relation_id=None):
    conf = config()
    relation_set(relation_id=relation_id,
                 username=conf['rabbit-user'], vhost=conf['rabbit-vhost'])


@hooks.hook('amqp-relation-changed')
@restart_on_change(restart_map())
def amqp_changed():
    if 'amqp' not in CONFIGS.complete_contexts():
        juju_log('amqp relation incomplete. Peer not ready?')
        return
    CONFIGS.write(CINDER_CONF)


@hooks.hook('amqp-relation-departed')
@restart_on_change(restart_map())
def amqp_departed():
    if 'amqp' not in CONFIGS.complete_contexts():
        juju_log('amqp relation incomplete. Peer not ready?')
        return
    CONFIGS.write(CINDER_CONF)


@hooks.hook('identity-service-relation-joined')
def identity_joined(rid=None):
    if config('vip') and not is_clustered():
        log('Defering registration until clustered', level=DEBUG)
        return

    settings = {}

    if not service_enabled('api'):
        juju_log('api service not enabled; skipping endpoint '
                 'registration')
        return

    cinder_release = os_release('cinder-common')
    if CompareOpenStackReleases(cinder_release) < 'pike':
        public_url = '{}:{}/v1/$(tenant_id)s'.format(
            canonical_url(CONFIGS, PUBLIC),
            config('api-listening-port')
        )
        internal_url = '{}:{}/v1/$(tenant_id)s'.format(
            canonical_url(CONFIGS, INTERNAL),
            config('api-listening-port')
        )
        admin_url = '{}:{}/v1/$(tenant_id)s'.format(
            canonical_url(CONFIGS, ADMIN),
            config('api-listening-port')
        )
        settings.update({
            'region': None,
            'service': None,
            'public_url': None,
            'internal_url': None,
            'admin_url': None,
            'cinder_region': config('region'),
            'cinder_service': 'cinder',
            'cinder_public_url': public_url,
            'cinder_internal_url': internal_url,
            'cinder_admin_url': admin_url,
        })
    if CompareOpenStackReleases(cinder_release) >= 'icehouse':
        # NOTE(jamespage) register v2 endpoint as well
        public_url = '{}:{}/v2/$(tenant_id)s'.format(
            canonical_url(CONFIGS, PUBLIC),
            config('api-listening-port')
        )
        internal_url = '{}:{}/v2/$(tenant_id)s'.format(
            canonical_url(CONFIGS, INTERNAL),
            config('api-listening-port')
        )
        admin_url = '{}:{}/v2/$(tenant_id)s'.format(
            canonical_url(CONFIGS, ADMIN),
            config('api-listening-port')
        )
        settings.update({
            'cinderv2_region': config('region'),
            'cinderv2_service': 'cinderv2',
            'cinderv2_public_url': public_url,
            'cinderv2_internal_url': internal_url,
            'cinderv2_admin_url': admin_url,
        })
    if CompareOpenStackReleases(cinder_release) >= 'pike':
        # NOTE(jamespage) register v3 endpoint as well
        public_url = '{}:{}/v3/$(tenant_id)s'.format(
            canonical_url(CONFIGS, PUBLIC),
            config('api-listening-port')
        )
        internal_url = '{}:{}/v3/$(tenant_id)s'.format(
            canonical_url(CONFIGS, INTERNAL),
            config('api-listening-port')
        )
        admin_url = '{}:{}/v3/$(tenant_id)s'.format(
            canonical_url(CONFIGS, ADMIN),
            config('api-listening-port')
        )
        settings.update({
            'cinderv3_region': config('region'),
            'cinderv3_service': 'cinderv3',
            'cinderv3_public_url': public_url,
            'cinderv3_internal_url': internal_url,
            'cinderv3_admin_url': admin_url,
        })
    relation_set(relation_id=rid, **settings)


@hooks.hook('identity-service-relation-changed')
@restart_on_change(restart_map())
def identity_changed():
    if 'identity-service' not in CONFIGS.complete_contexts():
        juju_log('identity-service relation incomplete. Peer not ready?')
        return
    CONFIGS.write(CINDER_API_CONF)
    configure_https()


@hooks.hook('ceph-relation-joined')
def ceph_joined():
    if not os.path.isdir('/etc/ceph'):
        os.mkdir('/etc/ceph')
    apt_install('ceph-common', fatal=True)


def get_ceph_request():
    service = service_name()
    rq = CephBrokerRq()
    replicas = config('ceph-osd-replication-count')
    rq.add_op_create_pool(name=service,
                          replica_count=replicas,
                          group="volumes")
    if config('restrict-ceph-pools'):
        rq.add_op_request_access_to_group(
            name="volumes",
            object_prefix_permissions={'class-read': ['rbd_children']},
            permission='rwx')
        rq.add_op_request_access_to_group(
            name="images",
            object_prefix_permissions={'class-read': ['rbd_children']},
            permission='rwx')
        rq.add_op_request_access_to_group(
            name="vms",
            object_prefix_permissions={'class-read': ['rbd_children']},
            permission='rwx')
    return rq


@hooks.hook('ceph-relation-changed')
@restart_on_change(restart_map())
def ceph_changed(relation_id=None):
    if 'ceph' not in CONFIGS.complete_contexts():
        juju_log('ceph relation incomplete. Peer not ready?')
        return

    service = service_name()
    if not ensure_ceph_keyring(service=service,
                               user='cinder', group='cinder'):
        juju_log('Could not create ceph keyring: peer not ready?')
        return

    if is_request_complete(get_ceph_request()):
        log('Request complete')
        set_ceph_env_variables(service=service)
        CONFIGS.write(CINDER_CONF)
        CONFIGS.write(ceph_config_file())
        # Ensure that cinder-volume is restarted since only now can we
        # guarantee that ceph resources are ready.
        if not is_unit_paused_set():
            service_restart('cinder-volume')
    else:
        send_request_if_needed(get_ceph_request())


@hooks.hook('ceph-relation-broken')
def ceph_broken():
    service = service_name()
    delete_keyring(service=service)
    CONFIGS.write_all()


@hooks.hook('cluster-relation-joined')
def cluster_joined(relation_id=None):
    settings = {}

    for addr_type in ADDRESS_TYPES:
        address = get_relation_ip(
            addr_type,
            cidr_network=config('os-{}-network'.format(addr_type)))
        if address:
            settings['{}-address'.format(addr_type)] = address

    settings['private-address'] = get_relation_ip('cluster')

    relation_set(relation_id=relation_id, relation_settings=settings)

    # Only do if this is fired by cluster rel
    if not relation_id:
        check_local_db_actions_complete()


@hooks.hook('cluster-relation-changed',
            'cluster-relation-departed')
@restart_on_change(restart_map(), stopstart=True)
def cluster_changed():
    check_local_db_actions_complete()
    CONFIGS.write_all()


@hooks.hook('ha-relation-joined')
def ha_joined(relation_id=None):
    cluster_config = get_hacluster_config()

    resources = {
        'res_cinder_haproxy': 'lsb:haproxy'
    }

    resource_params = {
        'res_cinder_haproxy': 'op monitor interval="5s"'
    }

    if config('dns-ha'):
        update_dns_ha_resource_params(relation_id=relation_id,
                                      resources=resources,
                                      resource_params=resource_params)
    else:
        vip_group = []
        for vip in cluster_config['vip'].split():
            if is_ipv6(vip):
                res_cinder_vip = 'ocf:heartbeat:IPv6addr'
                vip_params = 'ipv6addr'
            else:
                res_cinder_vip = 'ocf:heartbeat:IPaddr2'
                vip_params = 'ip'

            iface = (get_iface_for_address(vip) or
                     config('vip_iface'))
            netmask = (get_netmask_for_address(vip) or
                       config('vip_cidr'))

            if iface is not None:
                vip_key = 'res_cinder_{}_vip'.format(iface)
                if vip_key in vip_group:
                    if vip not in resource_params[vip_key]:
                        vip_key = '{}_{}'.format(vip_key, vip_params)
                    else:
                        log("Resource '%s' (vip='%s') already exists in "
                            "vip group - skipping" % (vip_key, vip), WARNING)
                        continue

                resources[vip_key] = res_cinder_vip
                resource_params[vip_key] = (
                    'params {ip}="{vip}" cidr_netmask="{netmask}"'
                    ' nic="{iface}"'.format(ip=vip_params,
                                            vip=vip,
                                            iface=iface,
                                            netmask=netmask)
                )
                vip_group.append(vip_key)

        if len(vip_group) >= 1:
            relation_set(relation_id=relation_id,
                         groups={'grp_cinder_vips': ' '.join(vip_group)})

    init_services = {
        'res_cinder_haproxy': 'haproxy'
    }
    clones = {
        'cl_cinder_haproxy': 'res_cinder_haproxy'
    }
    relation_set(relation_id=relation_id,
                 init_services=init_services,
                 corosync_bindiface=cluster_config['ha-bindiface'],
                 corosync_mcastport=cluster_config['ha-mcastport'],
                 resources=resources,
                 resource_params=resource_params,
                 clones=clones)


@hooks.hook('ha-relation-changed')
def ha_changed():
    clustered = relation_get('clustered')
    if not clustered or clustered in [None, 'None', '']:
        juju_log('ha_changed: hacluster subordinate not fully clustered.')
    else:
        juju_log('Cluster configured, notifying other services and updating '
                 'keystone endpoint configuration')
        for rid in relation_ids('identity-service'):
            identity_joined(rid=rid)


@hooks.hook('image-service-relation-changed')
@restart_on_change(restart_map())
def image_service_changed():
    CONFIGS.write(CINDER_CONF)


@hooks.hook('amqp-relation-broken',
            'identity-service-relation-broken',
            'image-service-relation-broken',
            'shared-db-relation-broken')
@restart_on_change(restart_map(), stopstart=True)
def relation_broken():
    CONFIGS.write_all()


@restart_on_change(restart_map(), stopstart=True)
def configure_https():
    '''Enables SSL API Apache config if appropriate and kicks identity-service
    with any required api updates.
    '''
    # need to write all to ensure changes to the entire request pipeline
    # propagate (c-api, haprxy, apache)
    CONFIGS.write_all()
    if 'https' in CONFIGS.complete_contexts():
        cmd = ['a2ensite', 'openstack_https_frontend']
        check_call(cmd)
    else:
        cmd = ['a2dissite', 'openstack_https_frontend']
        check_call(cmd)

    # TODO: improve this by checking if local CN certs are available
    # first then checking reload status (see LP #1433114).
    if not is_unit_paused_set():
        service_reload('apache2', restart_on_failure=True)

    for rid in relation_ids('identity-service'):
        identity_joined(rid=rid)


@hooks.hook('upgrade-charm')
@harden()
def upgrade_charm():
    apt_install(determine_packages(), fatal=True)
    for rel_id in relation_ids('amqp'):
        amqp_joined(relation_id=rel_id)
    update_nrpe_config()


@hooks.hook('storage-backend-relation-changed')
@hooks.hook('storage-backend-relation-broken')
@restart_on_change(restart_map())
def storage_backend():
    CONFIGS.write(CINDER_CONF)


@hooks.hook('backup-backend-relation-changed')
@hooks.hook('backup-backend-relation-broken')
@restart_on_change(restart_map())
def backup_backend():
    CONFIGS.write(CINDER_CONF)


@hooks.hook('nrpe-external-master-relation-joined',
            'nrpe-external-master-relation-changed')
def update_nrpe_config():
    # python-dbus is used by check_upstart_job
    apt_install('python-dbus')
    hostname = nrpe.get_nagios_hostname()
    current_unit = nrpe.get_nagios_unit_name()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    nrpe.copy_nrpe_checks()
    nrpe.add_init_service_checks(nrpe_setup, services(), current_unit)
    nrpe.add_haproxy_checks(nrpe_setup, current_unit)
    nrpe_setup.write()


@hooks.hook('update-status')
@harden()
def update_status():
    log('Updating status.')


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        juju_log('Unknown hook {} - skipping.'.format(e))
    assess_status(CONFIGS)
