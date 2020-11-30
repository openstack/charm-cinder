# Overview

The cinder charm deploys [Cinder][upstream-cinder], the Block Storage (volume)
service for OpenStack. The charm works alongside other Juju-deployed OpenStack
services.

# Usage

## Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

#### `openstack-origin`

The `openstack-origin` option states the software sources. A common value is an
OpenStack UCA release (e.g. 'cloud:bionic-ussuri' or 'cloud:focal-victoria').
See [Ubuntu Cloud Archive][wiki-uca]. The underlying host's existing apt
sources will be used if this option is not specified (this behaviour can be
explicitly chosen by using the value of 'distro').

## Deployment

This section includes two different deployment scenarios, each of which
requires these applications to be present: keystone, nova-cloud-controller,
nova-compute, rabbitmq-server, and a cloud database.

The database application is determined by the series. Prior to focal
[percona-cluster][percona-cluster-charm] is used, otherwise it is
[mysql-innodb-cluster][mysql-innodb-cluster-charm]. In the example deployment
below mysql-innodb-cluster has been chosen.

### Ceph-backed storage

Cinder can be backed by Ceph, which provides volumes with scalability and
redundancy.

> **Note**: Ceph is the recommended storage method for production Cinder
  deployments.

These instructions assume a pre-existing Ceph cluster.

File `cinder.yaml` contains the following:

```yaml
    cinder:
        block-device: None
```

Option `block-device` must be set to 'None' to disable the local block device.

Here, Cinder is deployed to a container on machine '1' and related to the Ceph
cluster via the cinder-ceph subordinate charm:

    juju deploy --to lxd:1 --config cinder.yaml cinder
    juju deploy cinder-ceph
    juju add-relation cinder-ceph:storage-backend cinder:storage-backend
    juju add-relation cinder-ceph:ceph ceph-mon:client
    juju add-relation cinder-ceph:ceph-access nova-compute:ceph-access

Proceed with a group of commands common to both scenarios:

    juju add-relation cinder:identity-service keystone:identity-service
    juju add-relation cinder:cinder-volume-service nova-cloud-controller:cinder-volume-service
    juju add-relation cinder:amqp rabbitmq-server:amqp

    juju deploy mysql-router cinder-mysql-router
    juju add-relation cinder-mysql-router:db-router mysql-innodb-cluster:db-router
    juju add-relation cinder-mysql-router:shared-db cinder:shared-db

### LVM/iSCSI-backed storage

Cinder can be backed by storage local to the cinder unit, where a block device
is used as an LVM physical volume. When a logical volume is created (`openstack
volume create`) it is exported to a VM via iSCSI when needed (`openstack server
add volume`).

> **Note**: LVM/iSCSI is intended for testing and small Cinder deployments.

A sample `cinder.yaml` file's contents:

```yaml
    cinder:
        block-device: sdc
```

> **Important**: Make sure the designated block device exists and is not
  currently in use.

Deploy Cinder:

    juju deploy --config cinder.yaml cinder

Proceed with the common group of commands from the Ceph scenario.

> **Note**: It has been reported that the LVM storage method may not properly
  initialise the physical volume and volume group. See bug
  [LP #1862392][lp-bug-1862392].

## High availability

When more than one unit is deployed with the [hacluster][hacluster-charm]
application the charm will bring up an HA active/active cluster.

There are two mutually exclusive high availability options: using virtual IP(s)
or DNS. In both cases the hacluster subordinate charm is used to provide the
Corosync and Pacemaker backend HA functionality.

See [OpenStack high availability][cdg-ha-apps] in the [OpenStack Charms
Deployment Guide][cdg] for details.

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

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions cinder`. If the charm is not
deployed then see file `actions.yaml`.

* `openstack-upgrade`
* `pause`
* `remove-services`
* `rename-volume-host`
* `resume`
* `security-checklist`
* `volume-host-add-driver`

## Policy overrides

Policy overrides is an advanced feature that allows an operator to override the
default policy of an OpenStack service. The policies that the service supports,
the defaults it implements in its code, and the defaults that a charm may
include should all be clearly understood before proceeding.

> **Caution**: It is possible to break the system (for tenants and other
  services) if policies are incorrectly applied to the service.

Policy statements are placed in a YAML file. This file (or files) is then (ZIP)
compressed into a single file and used as an application resource. The override
is then enabled via a Boolean charm option.

Here are the essential commands (filenames are arbitrary):

    zip overrides.zip override-file.yaml
    juju attach-resource cinder policyd-override=overrides.zip
    juju config cinder use-policyd-override=true

See [Policy overrides][cdg-policy-overrides] in the [OpenStack Charms
Deployment Guide][cdg] for a thorough treatment of this feature.

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-cinder].

For general charm questions refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[cdg-policy-overrides]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-policy-overrides.html
[juju-docs-spaces]: https://jaas.ai/docs/spaces
[juju-docs-actions]: https://jaas.ai/docs/actions
[lp-bugs-charm-cinder]: https://bugs.launchpad.net/charm-cinder/+filebug
[lp-bug-1862392]: https://bugs.launchpad.net/charm-cinder/+bug/1862392
[cdg-ha-apps]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-ha.html#ha-applications
[hacluster-charm]: https://jaas.ai/hacluster
[upstream-cinder]: https://docs.openstack.org/cinder/latest/
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[wiki-uca]: https://wiki.ubuntu.com/OpenStack/CloudArchive
[percona-cluster-charm]: https://jaas.ai/percona-cluster
[mysql-innodb-cluster-charm]: https://jaas.ai/mysql-innodb-cluster
