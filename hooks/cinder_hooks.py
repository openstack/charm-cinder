#!/usr/bin/python
import os
import sys
import uuid

from subprocess import (
    check_call,
)

from cinder_utils import (
    determine_packages,
    do_openstack_upgrade,
    git_install,
    juju_log,
    migrate_database,
    configure_lvm_storage,
    register_configs,
    restart_map,
    services,
    service_enabled,
    service_restart,
    set_ceph_env_variables,
    CLUSTER_RES,
    CINDER_CONF,
    CINDER_API_CONF,
    ceph_config_file,
    setup_ipv6,
    check_db_initialised,
    filesystem_mounted,
)

from charmhelpers.core.hookenv import (
    Hooks,
    UnregisteredHookError,
    config,
    is_relation_made,
    local_unit,
    relation_get,
    relation_ids,
    relation_set,
    service_name,
    unit_get,
    log,
    ERROR,
    INFO,
)

from charmhelpers.fetch import (
    apt_install,
    apt_update
)

from charmhelpers.core.host import (
    lsb_release,
    restart_on_change,
    service_reload,
    umount,
)

from charmhelpers.contrib.openstack.utils import (
    config_value_changed,
    configure_installation_source,
    git_install_requested,
    openstack_upgrade_available,
    sync_db_with_multi_ipv6_addresses,
    os_release,
)

from charmhelpers.contrib.storage.linux.ceph import (
    ensure_ceph_keyring,
    CephBrokerRq,
    CephBrokerRsp,
    delete_keyring,
)

from charmhelpers.contrib.hahelpers.cluster import (
    is_elected_leader,
    get_hacluster_config,
)

from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.contrib.network.ip import (
    get_iface_for_address,
    get_netmask_for_address,
    get_address_in_network,
    get_ipv6_addr,
    is_ipv6
)
from charmhelpers.contrib.openstack.ip import (
    canonical_url,
    PUBLIC, INTERNAL, ADMIN
)
from charmhelpers.contrib.openstack.context import ADDRESS_TYPES

from charmhelpers.contrib.charmsupport import nrpe

hooks = Hooks()

CONFIGS = register_configs()


@hooks.hook('install')
def install():
    execd_preinstall()
    conf = config()
    src = conf['openstack-origin']
    if (lsb_release()['DISTRIB_CODENAME'] == 'precise' and
            src == 'distro'):
        src = 'cloud:precise-folsom'
    configure_installation_source(src)

    apt_update()
    apt_install(determine_packages(), fatal=True)

    git_install(config('openstack-origin-git'))


@hooks.hook('config-changed')
@restart_on_change(restart_map(), stopstart=True)
def config_changed():
    conf = config()

    if conf['prefer-ipv6']:
        setup_ipv6()
        sync_db_with_multi_ipv6_addresses(config('database'),
                                          config('database-user'))

    e_mountpoint = config('ephemeral-unmount')
    if e_mountpoint and filesystem_mounted(e_mountpoint):
        umount(e_mountpoint)

    if (service_enabled('volume') and
            conf['block-device'] not in [None, 'None', 'none']):
        block_devices = conf['block-device'].split()
        configure_lvm_storage(block_devices,
                              conf['volume-group'],
                              conf['overwrite'] in ['true', 'True', True],
                              conf['remove-missing'],
                              conf['remove-missing-force'])

    if git_install_requested():
        if config_value_changed('openstack-origin-git'):
            git_install(config('openstack-origin-git'))
    elif not config('action-managed-upgrade'):
        if openstack_upgrade_available('cinder-common'):
            do_openstack_upgrade(configs=CONFIGS)
            # NOTE(jamespage) tell any storage-backends we just upgraded
            for rid in relation_ids('storage-backend'):
                relation_set(relation_id=rid,
                             upgrade_nonce=uuid.uuid4())

    # overwrite config is not in conf file. so We can't use restart_on_change
    if config_value_changed('overwrite'):
        service_restart('cinder-volume')

    CONFIGS.write_all()
    configure_https()
    update_nrpe_config()

    for rid in relation_ids('cluster'):
        cluster_joined(relation_id=rid)
    for r_id in relation_ids('ha'):
        ha_joined(relation_id=r_id)


@hooks.hook('shared-db-relation-joined')
def db_joined():
    if is_relation_made('pgsql-db'):
        # error, postgresql is used
        e = ('Attempting to associate a mysql database when there is already '
             'associated a postgresql one')
        log(e, level=ERROR)
        raise Exception(e)

    if config('prefer-ipv6'):
        sync_db_with_multi_ipv6_addresses(config('database'),
                                          config('database-user'))
    else:
        host = unit_get('private-address')
        conf = config()
        relation_set(database=conf['database'],
                     username=conf['database-user'],
                     hostname=host)


@hooks.hook('pgsql-db-relation-joined')
def pgsql_db_joined():
    if is_relation_made('shared-db'):
        # raise error
        e = ('Attempting to associate a postgresql database when there is'
             ' already associated a mysql one')
        log(e, level=ERROR)
        raise Exception(e)

    conf = config()
    relation_set(database=conf['database'])


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


@hooks.hook('pgsql-db-relation-changed')
@restart_on_change(restart_map())
def pgsql_db_changed():
    if 'pgsql-db' not in CONFIGS.complete_contexts():
        juju_log('pgsql-db relation incomplete. Peer not ready?')
        return
    CONFIGS.write(CINDER_CONF)
    if is_elected_leader(CLUSTER_RES):
        juju_log('Cluster leader, performing db sync')
        migrate_database()


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
    settings = {
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
    }
    if os_release('cinder-common') >= 'icehouse':
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

    settings = relation_get(rid=relation_id)
    if settings and 'broker_rsp' in settings:
        rsp = CephBrokerRsp(settings['broker_rsp'])
        # Non-zero return code implies failure
        if rsp.exit_code:
            log("Ceph broker request failed (rc=%s, msg=%s)" %
                (rsp.exit_code, rsp.exit_msg), level=ERROR)
            return

        log("Ceph broker request succeeded (rc=%s, msg=%s)" %
            (rsp.exit_code, rsp.exit_msg), level=INFO)
        set_ceph_env_variables(service=service)
        CONFIGS.write(CINDER_CONF)
        CONFIGS.write(ceph_config_file())
        # Ensure that cinder-volume is restarted since only now can we
        # guarantee that ceph resources are ready.
        service_restart('cinder-volume')
    else:
        rq = CephBrokerRq()
        replicas = config('ceph-osd-replication-count')
        rq.add_op_create_pool(name=service, replica_count=replicas)
        for rid in relation_ids('ceph'):
            relation_set(relation_id=rid, broker_req=rq.request)
            log("Request(s) sent to Ceph broker (rid=%s)" % (rid))


@hooks.hook('ceph-relation-broken')
def ceph_broken():
    service = service_name()
    delete_keyring(service=service)
    CONFIGS.write_all()


@hooks.hook('cluster-relation-joined')
def cluster_joined(relation_id=None):
    for addr_type in ADDRESS_TYPES:
        address = get_address_in_network(
            config('os-{}-network'.format(addr_type))
        )
        if address:
            relation_set(
                relation_id=relation_id,
                relation_settings={'{}-address'.format(addr_type): address}
            )

    # Only do if this is fired by cluster rel
    if not relation_id:
        check_db_initialised()

    if config('prefer-ipv6'):
        private_addr = get_ipv6_addr(exc_list=[config('vip')])[0]
        relation_set(relation_id=relation_id,
                     relation_settings={'private-address': private_addr})


@hooks.hook('cluster-relation-changed',
            'cluster-relation-departed')
@restart_on_change(restart_map(), stopstart=True)
def cluster_changed():
    check_db_initialised()
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
            'shared-db-relation-broken',
            'pgsql-db-relation-broken')
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
    service_reload('apache2', restart_on_failure=True)

    for rid in relation_ids('identity-service'):
        identity_joined(rid=rid)


@hooks.hook('upgrade-charm')
def upgrade_charm():
    for rel_id in relation_ids('amqp'):
        amqp_joined(relation_id=rel_id)
    update_nrpe_config()


@hooks.hook('storage-backend-relation-changed')
@hooks.hook('storage-backend-relation-broken')
@restart_on_change(restart_map())
def storage_backend():
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


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        juju_log('Unknown hook {} - skipping.'.format(e))
