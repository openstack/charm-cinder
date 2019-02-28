#!/usr/bin/env python3
#
# Copyright 2019 Canonical Ltd
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

import configparser
import os
import sys

sys.path.append('.')

import charmhelpers.contrib.openstack.audits as audits
from charmhelpers.contrib.openstack.audits import (
    openstack_security_guide,
)


# Via the openstack_security_guide above, we are running the following
# security assertions automatically:
#
# - Check-Block-01 - validate-file-ownership
# - Check-Block-02 - validate-file-permissions
# - Check-Block-03 - validate-uses-keystone
# - Check-Block-04 - validate-uses-tls-for-keystone


@audits.audit(audits.is_audit_type(audits.AuditType.OpenStackSecurityGuide),)
def uses_tls_for_nova(audit_options):
    """Validate that TLS is used to communicate with Nova.

    Security Guide Check Name: Check-Block-05

    :param audit_options: Dictionary of options for audit configuration
    :type audit_options: Dict
    :raises: AssertionError if the assertion fails.
    """
    section = audit_options['cinder-conf']['DEFAULT']
    nova_api_insecure = section.get("nova_api_insecure")
    assert "False" == nova_api_insecure, \
        "nova_api_insecure should be False"


@audits.audit(audits.is_audit_type(audits.AuditType.OpenStackSecurityGuide),)
def uses_tls_for_glance(audit_options):
    """Validate that TLS is used to communicate with Glance.

    Security Guide Check Name: Check-Block-06

    :param audit_options: Dictionary of options for audit configuration
    :type audit_options: Dict
    :raises: AssertionError if the assertion fails.
    """
    section = audit_options['cinder-conf']['DEFAULT']
    nova_api_insecure = section.get("glance_api_insecure")
    assert "False" == nova_api_insecure, \
        "nova_api_insecure should be False"
    glance_api_servers = section.get("glance_api_servers")
    assert glance_api_servers.startswith("https://"), \
        "glance_api_servers should use https"


@audits.audit(audits.is_audit_type(audits.AuditType.OpenStackSecurityGuide),)
def validate_nas_uses_secure_environment(audit_options):
    """Validate NAS security.

    Security Guide Check Name: Check-Block-07

    :param audit_options: Dictionary of options for audit configuration
    :type audit_options: Dict
    :raises: AssertionError if the assertion fails.
    """
    section = audit_options['cinder-conf']['DEFAULT']
    nas_secure_file_permissions = section.get('nas_secure_file_permissions')
    nas_secure_file_operations = section.get('nas_secure_file_operations')
    assert (nas_secure_file_permissions != "False" and
            nas_secure_file_operations != "False"), \
        "NAS is not using a secure environment"


@audits.audit(audits.is_audit_type(audits.AuditType.OpenStackSecurityGuide),)
def check_max_request_body_size(audit_options):
    """Validate max_request_body_size is set.

    Security Guide Check Name: Check-Block-08

    :param audit_options: Dictionary of options for audit configuration
    :type audit_options: Dict
    :raises: AssertionError if the assertion fails.
    """
    default = audit_options['cinder-conf']['DEFAULT']
    oslo_middleware = audit_options['cinder-conf'] \
        .get('oslo_middleware', {})
    assert (default.get('osapi_max_request_body_size') or
            oslo_middleware.get('max_request_body_size') == "114688"), \
        "max_request_body_size should be set"


@audits.audit(audits.is_audit_type(audits.AuditType.OpenStackSecurityGuide),)
def is_volume_encryption_enabled(audit_options):
    """Validate volume encryption is enabled in Cinder.

    Security Guide Check Name: Check-Block-09

    :param audit_options: Dictionary of options for audit configuration
    :type audit_options: Dict
    :raises: AssertionError if the assertion fails.
    """
    key_manager = audit_options['cinder-conf']['key_manager']
    assert key_manager.get('backend') is not None, \
        "key_manager.backend should be set"


def _config_file(path):
    """Read and parse config file at `path` as an ini file.

    :param path: Path of the file
    :type path: List[str]
    :returns: Parsed contents of the file at path
    :rtype Dict:
    """
    conf = configparser.ConfigParser()
    conf.read(os.path.join(*path))
    return dict(conf)


def main():
    config = {
        'config_path': '/etc/cinder',
        'config_file': 'cinder.conf',
        'audit_type': audits.AuditType.OpenStackSecurityGuide,
        'files': openstack_security_guide.FILE_ASSERTIONS['cinder'],
        'excludes': [
            'validate-uses-tls-for-glance',
        ],
    }
    config['cinder-conf'] = _config_file('/etc/cinder/cinder.conf')
    return audits.action_parse_results(audits.run(config))

if __name__ == "__main__":
    sys.exit(main())
