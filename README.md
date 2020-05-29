# Overview

This charm provides the Cinder volume service for OpenStack. It is intended to
be used alongside the other OpenStack components.

# Usage

## Deployment

Two deployment configurations will be shown. Both assume the existence of core
OpenStack services: mysql, rabbitmq-server, keystone, and
nova-cloud-controller.

### Storage backed by LVM-iSCSI

With this configuration, a block device (local to the cinder unit) is used as
an LVM physical volume. A logical volume is created (`openstack volume create`)
and exported to a cloud instance via iSCSI (`openstack server add volume`).

> **Note**: It is not recommended to use the LVM storage method for anything
  other than testing or for small non-production deployments.

A sample `cinder.yaml` file's contents:

```yaml
    cinder:
        block-device: sdc
```

> **Important**: Make sure the designated block device exists and is not
  currently in use.

Deploy and add relations in this way:

    juju deploy --config cinder.yaml cinder

    juju add-relation cinder:cinder-volume-service nova-cloud-controller:cinder-volume-service
    juju add-relation cinder:shared-db mysql:shared-db
    juju add-relation cinder:identity-service keystone:identity-service
    juju add-relation cinder:amqp rabbitmq-server:amqp

> **Note**: It has been reported that the LVM storage method may not properly
  initialise the physical volume and volume group. See bug
  [LP #1862392][lp-bug-1862392].

### Storage backed by Ceph

Here, storage volumes are backed by Ceph to allow for scalability and
redundancy. This is intended for large-scale production deployments. These
instructions assume a functioning Ceph cluster has been deployed to the cloud.

> **Note**: The Ceph storage method is the recommended method for production
  deployments.

File `cinder.yaml` contains the following:

```yaml
    cinder:
        block-device: None
```

Deploy and add relations as in the standard configuration (using the altered
YAML file). However, to use Ceph as the backend the intermediary cinder-ceph
charm is required:

    juju deploy cinder-ceph

Then add a relation from this charm to both Cinder and Ceph:

    juju add-relation cinder-ceph:storage-backend cinder:storage-backend
    juju add-relation cinder-ceph:ceph ceph-mon:client

### Juju storage

Juju storage can also be used to add block devices to cinder.  This uses LVM on
a block device that is presented to the cinder unit(s) locally, but can be from
any storage pool that Juju supports.  This is only recommended for small scale
deployments. Storage backed by Ceph should be used for larger deployments.

The cinder.yaml can be configured as:

```yaml
    cinder:
      options:
        block-device: None
      storage:
        block-devices: 'cinder,40G'
```

Setting `cinder.options.block-device = None` disables the local block device so
that Cinder will only be configured with the Juju storage device.

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
Actions allow specific operations to be performed on a per-unit basis.

### openstack-upgrade

Perform the OpenStack service upgrade. Configuration option
`action-managed-upgrade` must be set to 'True'.

### pause

Pause the cinder unit. This action will stop the Cinder service.

### remove-services

Remove unused services entities from the database after enabling HA with a
stateless backend such as the cinder-ceph application.

### rename-volume-host

Update the host attribute of volumes from currenthost to newhost.

### resume

Resume the cinder unit. This action will start the Cinder service if paused.

### security-checklist

Validate the running configuration against the OpenStack security guides
checklist.

### volume-host-add-driver

Update the 'os-vol-host-attr:host' volume attribute. Used for migrating volumes
to another backend.

## Policy Overrides

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

See appendix [Policy Overrides][cdg-appendix-n] in the [OpenStack Charms
Deployment Guide][cdg] for a thorough treatment of this feature.

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-cinder].

For general charm questions refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[cdg-appendix-n]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-policy-overrides.html
[juju-docs-spaces]: https://jaas.ai/docs/spaces
[juju-docs-actions]: https://jaas.ai/docs/actions
[lp-bugs-charm-cinder]: https://bugs.launchpad.net/charm-cinder/+filebug
[lp-bug-1862392]: https://bugs.launchpad.net/charm-cinder/+bug/1862392
[cdg-ha-apps]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-ha.html#ha-applications
[hacluster-charm]: https://jaas.ai/hacluster
