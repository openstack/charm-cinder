Overview
--------

This charm provides the Cinder volume service for OpenStack.  It is intended to
be used alongside the other OpenStack components, starting with the Folsom
release.

Cinder is made up of 3 separate services: an API service, a scheduler and a
volume service.  This charm allows them to be deployed in different
combination, depending on user preference and requirements.

This charm was developed to support deploying Folsom on both
Ubuntu Quantal and Ubuntu Precise.  Since Cinder is only available for
Ubuntu 12.04 via the Ubuntu Cloud Archive, deploying this charm to a
Precise machine will by default install Cinder and its dependencies from
the Cloud Archive.

Usage
-----

Cinder may be deployed in a number of ways.  This charm focuses on 3 main
configurations.  All require the existence of the other core OpenStack
services deployed via Juju charms, specifically: mysql, rabbitmq-server,
keystone and nova-cloud-controller.  The following assumes these services
have already been deployed.

Basic, all-in-one using local storage and iSCSI
===============================================

The api server, scheduler and volume service are all deployed into the same
unit.  Local storage will be initialized as a LVM phsyical device, and a volume
group initialized.  Instance volumes will be created locally as logical volumes
and exported to instances via iSCSI.  This is ideal for small-scale deployments
or testing:

    cat >cinder.cfg <<END
    cinder:
        block-device: sdc
        overwrite: true
    END
    juju deploy --config=cinder.cfg cinder
    juju add-relation cinder keystone
    juju add-relation cinder mysql
    juju add-relation cinder rabbitmq-server
    juju add-relation cinder nova-cloud-controller

Separate volume units for scale out, using local storage and iSCSI
==================================================================

Separating the volume service from the API service allows the storage pool
to easily scale without the added complexity that accompanies load-balancing
the API server.  When we've exhausted local storage on volume server, we can
simply add-unit to expand our capacity.  Future requests to allocate volumes
will be distributed across the pool of volume servers according to the
availability of storage space.

    cat >cinder.cfg <<END
    cinder-api:
        enabled-services: api, scheduler
    cinder-volume:
        enabled-services: volume
        block-device: sdc
        overwrite: true
    END
    juju deploy --config=cinder.cfg cinder cinder-api
    juju deploy --config=cinder.cfg cinder cinder-volume
    juju add-relation cinder-api mysql
    juju add-relation cinder-api rabbitmq-server
    juju add-relation cinder-api keystone
    juju add-relation cinder-api nova-cloud-controller
    juju add-relation cinder-volume mysql
    juju add-relation cinder-volume rabbitmq-server

    # When more storage is needed, simply add more volume servers.
    juju add-unit cinder-volume

All-in-one using Ceph-backed RBD volumes
========================================

All 3 services can be deployed to the same unit, but instead of relying
on local storage to back volumes an external Ceph cluster is used.  This
allows scalability and redundancy needs to be satisified and Cinder's RBD
driver used to create, export and connect volumes to instances.  This assumes
a functioning Ceph cluster has already been deployed using the official Ceph
charm and a relation exists between the Ceph service and the nova-compute
service.

    cat >cinder.cfg <<END
    cinder:
        block-device: None
    END
    juju deploy --config=cinder.cfg cinder
    juju add-relation cinder ceph
    juju add-relation cinder keystone
    juju add-relation cinder mysql
    juju add-relation cinder rabbitmq-server
    juju add-relation cinder nova-cloud-controller


Configuration
-------------

The default value for most config options should work for most deployments.

Users should be aware of three options, in particular:

openstack-origin:  Allows Cinder to be installed from a specific apt repository.
                   See config.yaml for a list of supported sources.

block-device:  When using local storage, a block device should be specified to
               back a LVM volume group.  It's important this device exists on
               all nodes that the service may be deployed to.

overwrite:  Whether or not to wipe local storage that of data that may prevent
            it from being initialized as a LVM phsyical device.  This includes
            filesystems and partition tables.  *CAUTION*

enabled-services:  Can be used to separate cinder services between service
                   service units (see previous section)

HA/Clustering
---------------------

There are two mutually exclusive high availability options: using virtual
IP(s) or DNS. In both cases, a relationship to hacluster is required which
provides the corosync back end HA functionality.

To use virtual IP(s) the clustered nodes must be on the same subnet such that
the VIP is a valid IP on the subnet for one of the node's interfaces and each
node has an interface in said subnet. The VIP becomes a highly-available API
endpoint.

At a minimum, the config option 'vip' must be set in order to use virtual IP
HA. If multiple networks are being used, a VIP should be provided for each
network, separated by spaces. Optionally, vip_iface or vip_cidr may be
specified.

To use DNS high availability there are several prerequisites. However, DNS HA
does not require the clustered nodes to be on the same subnet.
Currently the DNS HA feature is only available for MAAS 2.0 or greater
environments. MAAS 2.0 requires Juju 2.0 or greater. The clustered nodes must
have static or "reserved" IP addresses registered in MAAS. The DNS hostname(s)
must be pre-registered in MAAS before use with DNS HA.

At a minimum, the config option 'dns-ha' must be set to true and at least one
of 'os-public-hostname', 'os-internal-hostname' or 'os-internal-hostname' must
be set in order to use DNS HA. One or more of the above hostnames may be set.

The charm will throw an exception in the following circumstances:
If neither 'vip' nor 'dns-ha' is set and the charm is related to hacluster
If both 'vip' and 'dns-ha' are set as they are mutually exclusive
If 'dns-ha' is set and none of the os-{admin,internal,public}-hostname(s) are
set

Network Space support
---------------------

This charm supports the use of Juju Network Spaces, allowing the charm to be bound to network space configurations managed directly by Juju.  This is only supported with Juju 2.0 and above.

API endpoints can be bound to distinct network spaces supporting the network separation of public, internal and admin endpoints.

Access to the underlying MySQL instance can also be bound to a specific space using the shared-db relation.

To use this feature, use the --bind option when deploying the charm:

    juju deploy cinder --bind "public=public-space internal=internal-space admin=admin-space shared-db=internal-space"

alternatively these can also be provided as part of a juju native bundle configuration:

    cinder:
      charm: cs:xenial/cinder
      num_units: 1
      bindings:
        public: public-space
        admin: admin-space
        internal: internal-space
        shared-db: internal-space

NOTE: Spaces must be configured in the underlying provider prior to attempting to use them.

NOTE: Existing deployments using os-*-network configuration options will continue to function; these options are preferred over any network space binding provided if set.
