#!/usr/bin/python
import sys
import uuid

sys.path.append('hooks/')

from charmhelpers.contrib.openstack.utils import (
    do_action_openstack_upgrade,
)

from charmhelpers.core.hookenv import (
    relation_ids,
    relation_set,
)

from cinder_hooks import (
    config_changed,
    CONFIGS,
)

from cinder_utils import (
    do_openstack_upgrade,
)


def openstack_upgrade():
    """Upgrade packages to config-set Openstack version.

    If the charm was installed from source we cannot upgrade it.
    For backwards compatibility a config flag must be set for this
    code to run, otherwise a full service level upgrade will fire
    on config-changed."""

    if (do_action_openstack_upgrade('cinder-common',
                                    do_openstack_upgrade,
                                    CONFIGS)):
        # tell any storage-backends we just upgraded
        for rid in relation_ids('storage-backend'):
            relation_set(relation_id=rid,
                         upgrade_nonce=uuid.uuid4())
        config_changed()

if __name__ == '__main__':
    openstack_upgrade()
