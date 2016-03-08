#!/usr/bin/env python

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


def remove_services():
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


if __name__ == "__main__":
    remove_services()
