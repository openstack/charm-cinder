# Overview

The cinder charm deploys [Cinder][upstream-cinder], the Block Storage (volume)
service for OpenStack. The charm works alongside other Juju-deployed OpenStack
services.

# Usage

## Configuration

To display all configuration option information run `juju config
<application>`. If the application is not deployed then see the charm's
[Configure tab][cinder-configure] in the Charmhub. Finally, the [Juju
documentation][juju-docs-config-apps] provides general guidance on configuring
applications.

## Deployment

The cinder application requires the following applications to be present:
keystone, nova-cloud-controller, nova-compute, rabbitmq-server, and a cloud
database.

The database application is determined by the series. Prior to focal
[percona-cluster][percona-cluster-charm] is used, otherwise it is
[mysql-innodb-cluster][mysql-innodb-cluster-charm]. In the example deployment
below mysql-innodb-cluster has been chosen.

Deploy Cinder itself (here, to a container on machine '1'), add relations to
the core cloud applications, and then connect it to the cloud database:

    juju deploy --to lxd:1 --config cinder.yaml cinder
    juju add-relation cinder:identity-service keystone:identity-service
    juju add-relation cinder:cinder-volume-service nova-cloud-controller:cinder-volume-service
    juju add-relation cinder:amqp rabbitmq-server:amqp

    juju deploy mysql-router cinder-mysql-router
    juju add-relation cinder-mysql-router:db-router mysql-innodb-cluster:db-router
    juju add-relation cinder-mysql-router:shared-db cinder:shared-db

Multiple backend storage solutions are described next.

### Ceph-backed storage

Cinder can be backed by Ceph, which is the recommended storage method for
production Cinder deployments. This functionality is provided by the
[cinder-ceph][cinder-ceph-charm] subordinate charm.

### LVM-backed storage

Cinder can be backed by storage local to the cinder unit, where local block
devices are used as LVM physical volumes, and volumes are offered via iSCSI.
This functionality is provided by the [cinder-lvm][cinder-lvm-charm]
subordinate charm.

> **Note**: Built-in support for LVM in the cinder charm is deprecated.

### NetApp-backed storage

Cinder can be backed by a NetApp appliance local to the cinder unit, where
volumes are offered via iSCSI or NFS. This functionality is provided by the
[cinder-netapp][cinder-netapp-charm] subordinate charm.

### Pure Storage-backed storage

Cinder can be backed by a Pure Storage appliance reachable by its API endpoint.
This functionality is provided by the
[cinder-purestorage][cinder-purestorage-charm] subordinate charm.

## High availability

This charm supports high availability via HAcluster.

When more than one unit is deployed with the [hacluster][hacluster-charm]
application the charm will bring up an HA active/active cluster.

## Network spaces

This charm supports the use of Juju [network spaces][juju-docs-spaces] (Juju
`v.2.0`). This feature optionally allows specific types of the application's
network traffic to be bound to subnets that the underlying hardware is
connected to.

> **Note**: Spaces must be configured in the backing cloud prior to deployment.

API endpoints can be bound to distinct network spaces supporting the network
separation of public, internal, and admin endpoints.

Access to the underlying MySQL instance can also be bound to a specific space
using the shared-db relation.

For example, providing that spaces 'public-space', 'internal-space', and
'admin-space' exist, the deploy command above could look like this:

    juju deploy --config cinder.yaml cinder \
       --bind "public=public-space internal=internal-space admin=admin-space shared-db=internal-space"

Alternatively, configuration can be provided as part of a bundle:

```yaml
    cinder:
      charm: cs:cinder
      num_units: 1
      bindings:
        public: public-space
        internal: internal-space
        admin: admin-space
        shared-db: internal-space
```

> **Note**: Existing cinder units configured with the `os-admin-network`,
  `os-internal-network`, or `os-public-network` options will continue to honour
  them. Furthermore, these options override any space bindings, if set.

## Actions

This charm supports actions.

[Actions][juju-docs-actions] allow specific operations to be performed on a
per-unit basis. To display actions and their descriptions run `juju actions
--schema <application>`. If the application is not deployed then see the
charm's [Actions tab][cinder-actions] in the Charmhub.

## Policy overrides

This charm supports the policy overrides feature.

Policy overrides allow an operator to override the default policy of an
OpenStack service. See [Policy overrides][cg-policy-overrides] for more
information on this feature.

# Documentation

The OpenStack Charms project maintains two documentation guides:

* [OpenStack Charm Guide][cg]: the primary source of information for
  OpenStack charms
* [OpenStack Charms Deployment Guide][cdg]: a step-by-step guide for
  deploying OpenStack with charms

# Bugs

Please report bugs on [Launchpad][cinder-filebug].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[cg-policy-overrides]: https://docs.openstack.org/charm-guide/latest/admin/policy-overrides.html
[juju-docs-spaces]: https://juju.is/docs/olm/network-spaces
[juju-docs-actions]: https://juju.is/docs/olm/working-with-actions
[cinder-actions]: https://charmhub.io/cinder/actions
[juju-docs-config-apps]: https://juju.is/docs/olm/configure-an-application
[cinder-configure]: https://charmhub.io/cinder/configure
[cinder-filebug]: https://bugs.launchpad.net/charm-cinder/+filebug
[lp-bug-1862392]: https://bugs.launchpad.net/charm-cinder/+bug/1862392
[hacluster-charm]: https://charmhub.io/hacluster
[cinder-lvm-charm]: https://charmhub.io/cinder-lvm
[cinder-netapp-charm]: https://charmhub.io/cinder-netapp
[cinder-purestorage-charm]: https://charmhub.io/cinder-purestorage
[percona-cluster-charm]: https://charmhub.io/percona-cluster
[mysql-innodb-cluster-charm]: https://charmhub.io/mysql-innodb-cluster
[upstream-cinder]: https://docs.openstack.org/cinder/latest/
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[wiki-uca]: https://wiki.ubuntu.com/OpenStack/CloudArchive
