import os
import sys

from mock import patch, MagicMock

os.environ['JUJU_UNIT_NAME'] = 'cinder'

# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
mock_apt = MagicMock()
sys.modules['apt'] = mock_apt
mock_apt.apt_pkg = MagicMock()

from test_utils import RESTART_MAP, CharmTestCase

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    with patch('cinder_utils.register_configs') as register_configs:
        with patch('cinder_utils.restart_map') as restart_map:
            restart_map.return_value = RESTART_MAP
            import git_reinstall

TO_PATCH = [
    'config',
]


openstack_origin_git = \
    """repositories:
         - {name: requirements,
            repository: 'https://git.openstack.org/openstack/requirements',
            branch: stable/juno}
         - {name: cinder,
            repository: 'https://git.openstack.org/openstack/cinder',
            branch: stable/juno}"""


class TestCinderActions(CharmTestCase):

    def setUp(self):
        super(TestCinderActions, self).setUp(git_reinstall, TO_PATCH)
        self.config.side_effect = self.test_config.get

    @patch.object(git_reinstall, 'action_set')
    @patch.object(git_reinstall, 'action_fail')
    @patch.object(git_reinstall, 'git_install')
    @patch.object(git_reinstall, 'config_changed')
    @patch('charmhelpers.contrib.openstack.utils.config')
    def test_git_reinstall(self, _config, config_changed, git_install,
                           action_fail, action_set):
        _config.return_value = openstack_origin_git
        self.test_config.set('openstack-origin-git', openstack_origin_git)

        git_reinstall.git_reinstall()

        git_install.assert_called_with(openstack_origin_git)
        self.assertTrue(git_install.called)
        self.assertTrue(config_changed.called)
        self.assertFalse(action_set.called)
        self.assertFalse(action_fail.called)

    @patch.object(git_reinstall, 'action_set')
    @patch.object(git_reinstall, 'action_fail')
    @patch.object(git_reinstall, 'git_install')
    @patch.object(git_reinstall, 'config_changed')
    @patch('charmhelpers.contrib.openstack.utils.config')
    def test_git_reinstall_not_configured(self, _config, config_changed,
                                          git_install, action_fail,
                                          action_set):
        _config.return_value = None

        git_reinstall.git_reinstall()

        msg = 'openstack-origin-git is not configured'
        action_fail.assert_called_with(msg)
        self.assertFalse(git_install.called)
        self.assertFalse(action_set.called)

    @patch.object(git_reinstall, 'action_set')
    @patch.object(git_reinstall, 'action_fail')
    @patch.object(git_reinstall, 'git_install')
    @patch.object(git_reinstall, 'config_changed')
    @patch('traceback.format_exc')
    @patch('charmhelpers.contrib.openstack.utils.config')
    def test_git_reinstall_exception(self, _config, format_exc, config_changed,
                                     git_install, action_fail, action_set):
        _config.return_value = openstack_origin_git
        e = OSError('something bad happened')
        git_install.side_effect = e
        traceback = (
            "Traceback (most recent call last):\n"
            "  File \"actions/git_reinstall.py\", line 37, in git_reinstall\n"
            "    git_install(config(\'openstack-origin-git\'))\n"
            "  File \"/usr/lib/python2.7/dist-packages/mock.py\", line 964, in __call__\n"  # noqa
            "    return _mock_self._mock_call(*args, **kwargs)\n"
            "  File \"/usr/lib/python2.7/dist-packages/mock.py\", line 1019, in _mock_call\n"  # noqa
            "    raise effect\n"
            "OSError: something bad happened\n")
        format_exc.return_value = traceback

        git_reinstall.git_reinstall()

        msg = 'git-reinstall resulted in an unexpected error'
        action_fail.assert_called_with(msg)
        action_set.assert_called_with({'traceback': traceback})
