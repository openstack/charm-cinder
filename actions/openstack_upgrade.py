#!/usr/bin/env python3
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


_path = os.path.dirname(os.path.realpath(__file__))
_hooks = os.path.abspath(os.path.join(_path, '../hooks'))
_root = os.path.abspath(os.path.join(_path, '..'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)

_add_path(_hooks)
_add_path(_root)

from charmhelpers.contrib.openstack.utils import (
    do_action_openstack_upgrade,
    is_unit_paused_set,
)

from charmhelpers.core.hookenv import (
    relation_ids,
    relation_set,
)

import cinder_hooks

from cinder_utils import (
    do_openstack_upgrade,
    register_configs,
    resume_unit_helper,
)


def openstack_upgrade():
    """Upgrade packages to config-set Openstack version.

    If the charm was installed from source we cannot upgrade it.
    For backwards compatibility a config flag must be set for this
    code to run, otherwise a full service level upgrade will fire
    on config-changed."""

    if (do_action_openstack_upgrade('cinder-common',
                                    do_openstack_upgrade,
                                    configs=None)):
        # tell any storage-backends we just upgraded
        for rid in relation_ids('storage-backend'):
            relation_set(relation_id=rid,
                         upgrade_nonce=uuid.uuid4())

        # Force reload to get any chances resulting from upgrade.
        # See LP 1726527.
        cinder_hooks.CONFIGS = register_configs()
        # If we are paused, resume LP Bug #1824545
        if is_unit_paused_set():
            resume_unit_helper(cinder_hooks.register_configs())
        cinder_hooks.config_changed()

if __name__ == '__main__':
    openstack_upgrade()
