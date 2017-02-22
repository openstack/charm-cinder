#!/usr/bin/env python
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
import traceback
import subprocess

sys.path.append('hooks/')

from cinder import context
from cinder import db
from cinder.db.sqlalchemy.api import model_query, get_session
from cinder.db.sqlalchemy import models

from charmhelpers.contrib.openstack.utils import os_release

from sqlalchemy import and_
from charmhelpers.core.hookenv import (
    action_set,
    action_fail,
    action_get,
    log,
)

DEFAULT_SERVICES = (
    "cinder",
    "cinder@cinder-ceph",
)

try:
    from cinder import flags
    cfg = flags.FLAGS
except ImportError:
    from cinder.common.config import CONF
    cfg = CONF


def load_config_file(conf):
    cfg(args=[], project='cinder', default_config_files=[conf])


def cinder_manage_remove(binary, hostname):
    return subprocess.check_call(["cinder-manage", "service", "remove", binary,
                                  hostname])


def cinder_manage_volume_update_host(currenthost, newhost):
    return subprocess.check_call(["cinder-manage", "volume", "update_host",
                                  "--currenthost", currenthost,
                                  "--newhost", newhost])


def remove_services(args):
    load_config_file(os.path.join(os.path.sep, "etc", "cinder", "cinder.conf"))

    host = action_get(key="host")
    services = model_query({}, models.Service, read_deleted="no",
                           session=get_session())

    if host not in ("unused", "",):
        services = services.filter(models.Service.host == host)
    else:
        ands = []
        for service in DEFAULT_SERVICES:
            ands.append(and_(models.Service.host != service))
        services = services.filter(*ands)

    removed_services = []
    ctxt = context.get_admin_context()

    for service in services.all():
        log("Removing service:%d, hostname:%s" % (service.id, service.host))
        try:
            if os_release("cinder") >= "liberty":
                cinder_manage_remove(service.binary, service.host)
            else:
                db.service_destroy(ctxt, service.id)
        except:
            action_set({'traceback': traceback.format_exc()})
            action_fail("Cannot remove service: %s" % service.host)
        else:
            removed_services.append(service.host)

    action_set({'removed': ",".join(removed_services)})


def _rename_volume_host(currenthost, newhost):
    load_config_file(os.path.join(os.path.sep, "etc", "cinder", "cinder.conf"))
    services = model_query({}, models.Service, read_deleted="no",
                           session=get_session())
    services = services.filter(models.Service.host == currenthost)
    if services.all():
        try:
            cinder_manage_volume_update_host(currenthost, newhost)
        except:
            action_set({'traceback': traceback.format_exc()})
            action_fail("Cannot update host {}".format(currenthost))
    else:
        action_fail(
            "Cannot update host attribute from {}, {} not found".format(
                currenthost,
                currenthost))


def rename_volume_host(args):
    action_args = action_get()
    _rename_volume_host(action_args['currenthost'], action_args['newhost'])


def volume_host_add_driver(args):
    action_args = action_get()
    newhost = "{}@{}".format(action_args['currenthost'], action_args['driver'])
    if action_args.get('volume-backend-name'):
        newhost = newhost + '#' + action_args['volume-backend-name']
    _rename_volume_host(action_args['currenthost'], newhost)
