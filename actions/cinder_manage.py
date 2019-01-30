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
import subprocess
import sys
import traceback


_path = os.path.dirname(os.path.realpath(__file__))
_hooks = os.path.abspath(os.path.join(_path, '../hooks'))
_root = os.path.abspath(os.path.join(_path, '..'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)

_add_path(_hooks)
_add_path(_root)

from charmhelpers.contrib.openstack.utils import (
    CompareOpenStackReleases,
    os_release,
)

from charmhelpers.core.hookenv import (
    action_fail,
    action_get,
    action_set,
    log,
)

DEFAULT_SERVICES = (
    "cinder",
    "cinder@cinder-ceph",
)


class CinderService():

    def __init__(self, binary, host,
                 zone, status, state):
        self.binary = binary
        self.host = host
        self.zone = zone
        self.status = status
        self.active = (state != 'XXX')


def cinder_manage_remove(binary, hostname):
    """Call cinder-manage service remove <binary> <hostname>

    :param binary: The binary (executable) for the service to remove
    :type binary: str
    :param hostname: The host name associated with the binary
    :type hostname: str
    :returns: 0 if the cinder-manage service command succeeds
    :rtype: int
    :raises: subprocess.CalledProcessError if the call fails
    """
    return (subprocess
            .check_call(["cinder-manage",
                         "service",
                         "remove",
                         binary,
                         hostname]))


def cinder_manage_volume_update_host(currenthost, newhost):
    """Call cinder-manage volume update_host --currenthost <current-host> \\
        --newhost <newhost>

    :param currenthost: the hostname to update to newhost
    :type currenthost: str
    :param newhost: the new name to update the host to
    :type newhost: str
    :returns: 0 if the cinder-manage volume command succeeds
    :rtype: int
    :raises: subprocess.CalledProcessError if the call fails
    """
    return (subprocess
            .check_call(["cinder-manage",
                         "volume",
                         "update_host",
                         "--currenthost", currenthost,
                         "--newhost", newhost]))


def cinder_manage_service_list():
    service_list = []
    services = subprocess.check_output(
        ["cinder-manage", "service", "list"]).decode('UTF-8')
    for service in services.splitlines():
        if not service.startswith('cinder'):
            continue
        service_list.append(CinderService(*service.split()[:5]))
    return service_list


def remove_services(args):
    host = action_get(key="host")
    services = cinder_manage_service_list()

    if host not in ("unused", "",):
        services = [s for s in services if s.host == host]
    else:
        services = [s for s in services if s.host not in DEFAULT_SERVICES]

    removed_services = []

    for service in services:
        log("Removing binary:{}, hostname:{}"
            .format(service.binary, service.host))
        try:
            if CompareOpenStackReleases(os_release("cinder")) >= "liberty":
                cinder_manage_remove(service.binary, service.host)
            else:
                action_fail("Cannot remove service: {}".format(service.host))
        except:
            action_set({'traceback': traceback.format_exc()})
            action_fail("Cannot remove service: {}".format(service.host))
        else:
            removed_services.append(service.host)

    action_set({'removed': ",".join(removed_services)})


def _rename_volume_host(currenthost, newhost):
    services = cinder_manage_service_list()
    services = [s for s in services if s.host == currenthost]
    if services:
        try:
            cinder_manage_volume_update_host(currenthost, newhost)
        except:
            action_set({'traceback': traceback.format_exc()})
            action_fail("Cannot update host {}".format(currenthost))
    else:
        action_fail("Cannot update host attribute from {}, {} not found"
                    .format(currenthost, currenthost))


def rename_volume_host(args):
    action_args = action_get()
    _rename_volume_host(action_args['currenthost'], action_args['newhost'])


def volume_host_add_driver(args):
    action_args = action_get()
    newhost = "{}@{}".format(action_args['currenthost'], action_args['driver'])
    if action_args.get('volume-backend-name'):
        newhost = newhost + '#' + action_args['volume-backend-name']
    _rename_volume_host(action_args['currenthost'], newhost)
