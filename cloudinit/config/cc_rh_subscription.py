# vi: ts=4 expandtab
#
#    Copyright (C) 2015 Red Hat, Inc.
#
#    Author: Brent Baude <bbaude@redhat.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License version 3, as
#    published by the Free Software Foundation.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

from cloudinit import util


def handle(_name, cfg, _cloud, log, _args):
    sm = SubscriptionManager(cfg)
    sm.log = log
    if not sm.is_registered:
        try:
            verify, verify_msg = sm._verify_keys()
            if verify is not True:
                raise SubscriptionError(verify_msg)
            cont = sm.rhn_register()
            if not cont:
                raise SubscriptionError("Registration failed or did not "
                                        "run completely")

            # Splitting up the registration, auto-attach, and servicelevel
            # commands because the error codes, messages from subman are not
            # specific enough.

            # Attempt to change the service level
            if sm.auto_attach and sm.servicelevel is not None:
                if not sm._set_service_level():
                    raise SubscriptionError("Setting of service-level "
                                            "failed")
                else:
                    sm.log.debug("Completed auto-attach with service level")
            elif sm.auto_attach:
                if not sm._set_auto_attach():
                    raise SubscriptionError("Setting auto-attach failed")
                else:
                    sm.log.debug("Completed auto-attach")

            if sm.pools is not None:
                if not isinstance(sm.pools, list):
                    pool_fail = "Pools must in the format of a list"
                    raise SubscriptionError(pool_fail)

                return_stat = sm.addPool(sm.pools)
                if not return_stat:
                    raise SubscriptionError("Unable to attach pools {0}"
                                            .format(sm.pools))
            if (sm.enable_repo is not None) or (sm.disable_repo is not None):
                return_stat = sm.update_repos(sm.enable_repo, sm.disable_repo)
                if not return_stat:
                    raise SubscriptionError("Unable to add or remove repos")
            sm.log_success("rh_subscription plugin completed successfully")
        except SubscriptionError as e:
            sm.log_warn(str(e))
            sm.log_warn("rh_subscription plugin did not complete successfully")
    else:
        sm.log_success("System is already registered")


class SubscriptionError(Exception):
    pass


class SubscriptionManager(object):
    valid_rh_keys = ['org', 'activation-key', 'username', 'password',
                     'disable-repo', 'enable-repo', 'add-pool',
                     'rhsm-baseurl', 'server-hostname',
                     'auto-attach', 'service-level']

    def __init__(self, cfg):
        self.cfg = cfg
        self.rhel_cfg = self.cfg.get('rh_subscription', {})
        self.rhsm_baseurl = self.rhel_cfg.get('rhsm-baseurl')
        self.server_hostname = self.rhel_cfg.get('server-hostname')
        self.pools = self.rhel_cfg.get('add-pool')
        self.activation_key = self.rhel_cfg.get('activation-key')
        self.org = self.rhel_cfg.get('org')
        self.userid = self.rhel_cfg.get('username')
        self.password = self.rhel_cfg.get('password')
        self.auto_attach = self.rhel_cfg.get('auto-attach')
        self.enable_repo = self.rhel_cfg.get('enable-repo')
        self.disable_repo = self.rhel_cfg.get('disable-repo')
        self.servicelevel = self.rhel_cfg.get('service-level')
        self.subman = ['subscription-manager']
        self.is_registered = self._is_registered()

    def log_success(self, msg):
        '''Simple wrapper for logging info messages. Useful for unittests'''
        self.log.info(msg)

    def log_warn(self, msg):
        '''Simple wrapper for logging warning messages. Useful for unittests'''
        self.log.warn(msg)

    def _verify_keys(self):
        '''
        Checks that the keys in the rh_subscription dict from the user-data
        are what we expect.
        '''

        for k in self.rhel_cfg:
            if k not in self.valid_rh_keys:
                bad_key = "{0} is not a valid key for rh_subscription. "\
                          "Valid keys are: "\
                          "{1}".format(k, ', '.join(self.valid_rh_keys))
                return False, bad_key

        # Check for bad auto-attach value
        if (self.auto_attach is not None) and \
                not (util.is_true(self.auto_attach) or
                     util.is_false(self.auto_attach)):
            not_bool = "The key auto-attach must be a boolean value "\
                       "(True/False "
            return False, not_bool

        if (self.servicelevel is not None) and \
                ((not self.auto_attach)
                 or (util.is_false(str(self.auto_attach)))):

            no_auto = ("The service-level key must be used in conjunction "
                       "with the auto-attach key.  Please re-run with "
                       "auto-attach: True")
            return False, no_auto
        return True, None

    def _is_registered(self):
        '''
        Checks if the system is already registered and returns
        True if so, else False
        '''
        cmd = ['identity']

        try:
            self._sub_man_cli(cmd)
        except util.ProcessExecutionError:
            return False

        return True

    def _sub_man_cli(self, cmd, logstring_val=False):
        '''
        Uses the prefered cloud-init subprocess def of util.subp
        and runs subscription-manager.  Breaking this to a
        separate function for later use in mocking and unittests
        '''
        cmd = self.subman + cmd
        return util.subp(cmd, logstring=logstring_val)

    def rhn_register(self):
        '''
        Registers the system by userid and password or activation key
        and org.  Returns True when successful False when not.
        '''

        if (self.activation_key is not None) and (self.org is not None):
            # register by activation key
            cmd = ['register', '--activationkey={0}'.
                   format(self.activation_key), '--org={0}'.format(self.org)]

            # If the baseurl and/or server url are passed in, we register
            # with them.

            if self.rhsm_baseurl is not None:
                cmd.append("--baseurl={0}".format(self.rhsm_baseurl))

            if self.server_hostname is not None:
                cmd.append("--serverurl={0}".format(self.server_hostname))

            try:
                return_out, return_err = self._sub_man_cli(cmd,
                                                           logstring_val=True)
            except util.ProcessExecutionError as e:
                if e.stdout == "":
                    self.log_warn("Registration failed due "
                                  "to: {0}".format(e.stderr))
                return False

        elif (self.userid is not None) and (self.password is not None):
            # register by username and password
            cmd = ['register', '--username={0}'.format(self.userid),
                   '--password={0}'.format(self.password)]

            # If the baseurl and/or server url are passed in, we register
            # with them.

            if self.rhsm_baseurl is not None:
                cmd.append("--baseurl={0}".format(self.rhsm_baseurl))

            if self.server_hostname is not None:
                cmd.append("--serverurl={0}".format(self.server_hostname))

            # Attempting to register the system only
            try:
                return_out, return_err = self._sub_man_cli(cmd,
                                                           logstring_val=True)
            except util.ProcessExecutionError as e:
                if e.stdout == "":
                    self.log_warn("Registration failed due "
                                  "to: {0}".format(e.stderr))
                return False

        else:
            self.log_warn("Unable to register system due to incomplete "
                          "information.")
            self.log_warn("Use either activationkey and org *or* userid "
                          "and password")
            return False

        reg_id = return_out.split("ID: ")[1].rstrip()
        self.log.debug("Registered successfully with ID {0}".format(reg_id))
        return True

    def _set_service_level(self):
        cmd = ['attach', '--auto', '--servicelevel={0}'
               .format(self.servicelevel)]

        try:
            return_out, return_err = self._sub_man_cli(cmd)
        except util.ProcessExecutionError as e:
            if e.stdout.rstrip() != '':
                for line in e.stdout.split("\n"):
                    if line is not '':
                        self.log_warn(line)
            else:
                self.log_warn("Setting the service level failed with: "
                              "{0}".format(e.stderr.strip()))
            return False
        for line in return_out.split("\n"):
            if line is not "":
                self.log.debug(line)
        return True

    def _set_auto_attach(self):
        cmd = ['attach', '--auto']
        try:
            return_out, return_err = self._sub_man_cli(cmd)
        except util.ProcessExecutionError:
            self.log_warn("Auto-attach failed with: "
                          "{0}]".format(return_err.strip()))
            return False
        for line in return_out.split("\n"):
            if line is not "":
                self.log.debug(line)
        return True

    def _getPools(self):
        '''
        Gets the list pools for the active subscription and returns them
        in list form.
        '''
        available = []
        consumed = []

        # Get all available pools
        cmd = ['list', '--available', '--pool-only']
        results, errors = self._sub_man_cli(cmd)
        available = (results.rstrip()).split("\n")

        # Get all consumed pools
        cmd = ['list', '--consumed', '--pool-only']
        results, errors = self._sub_man_cli(cmd)
        consumed = (results.rstrip()).split("\n")

        return available, consumed

    def _getRepos(self):
        '''
        Obtains the current list of active yum repositories and returns
        them in list form.
        '''

        cmd = ['repos', '--list-enabled']
        return_out, return_err = self._sub_man_cli(cmd)
        active_repos = []
        for repo in return_out.split("\n"):
            if "Repo ID:" in repo:
                active_repos.append((repo.split(':')[1]).strip())

        cmd = ['repos', '--list-disabled']
        return_out, return_err = self._sub_man_cli(cmd)

        inactive_repos = []
        for repo in return_out.split("\n"):
            if "Repo ID:" in repo:
                inactive_repos.append((repo.split(':')[1]).strip())
        return active_repos, inactive_repos

    def addPool(self, pools):
        '''
        Takes a list of subscription pools and "attaches" them to the
        current subscription
        '''

        # An empty list was passed
        if len(pools) == 0:
            self.log.debug("No pools to attach")
            return True

        pool_available, pool_consumed = self._getPools()
        pool_list = []
        cmd = ['attach']
        for pool in pools:
            if (pool not in pool_consumed) and (pool in pool_available):
                pool_list.append('--pool={0}'.format(pool))
            else:
                self.log_warn("Pool {0} is not available".format(pool))
        if len(pool_list) > 0:
            cmd.extend(pool_list)
            try:
                self._sub_man_cli(cmd)
                self.log.debug("Attached the following pools to your "
                               "system: %s" % (", ".join(pool_list))
                               .replace('--pool=', ''))
                return True
            except util.ProcessExecutionError as e:
                self.log_warn("Unable to attach pool {0} "
                              "due to {1}".format(pool, e))
                return False

    def update_repos(self, erepos, drepos):
        '''
        Takes a list of yum repo ids that need to be disabled or enabled; then
        it verifies if they are already enabled or disabled and finally
        executes the action to disable or enable
        '''

        if (erepos is not None) and (not isinstance(erepos, list)):
            self.log_warn("Repo IDs must in the format of a list.")
            return False

        if (drepos is not None) and (not isinstance(drepos, list)):
            self.log_warn("Repo IDs must in the format of a list.")
            return False

        # Bail if both lists are not populated
        if (len(erepos) == 0) and (len(drepos) == 0):
            self.log.debug("No repo IDs to enable or disable")
            return True

        active_repos, inactive_repos = self._getRepos()
        # Creating a list of repoids to be enabled
        enable_list = []
        enable_list_fail = []
        for repoid in erepos:
            if (repoid in inactive_repos):
                enable_list.append("--enable={0}".format(repoid))
            else:
                enable_list_fail.append(repoid)

        # Creating a list of repoids to be disabled
        disable_list = []
        disable_list_fail = []
        for repoid in drepos:
            if repoid in active_repos:
                disable_list.append("--disable={0}".format(repoid))
            else:
                disable_list_fail.append(repoid)

        # Logging any repos that are already enabled or disabled
        if len(enable_list_fail) > 0:
            for fail in enable_list_fail:
                # Check if the repo exists or not
                if fail in active_repos:
                    self.log.debug("Repo {0} is already enabled".format(fail))
                else:
                    self.log_warn("Repo {0} does not appear to "
                                  "exist".format(fail))
        if len(disable_list_fail) > 0:
            for fail in disable_list_fail:
                self.log.debug("Repo {0} not disabled "
                               "because it is not enabled".format(fail))

        cmd = ['repos']
        if len(enable_list) > 0:
            cmd.extend(enable_list)
        if len(disable_list) > 0:
            cmd.extend(disable_list)

        try:
            self._sub_man_cli(cmd)
        except util.ProcessExecutionError as e:
            self.log_warn("Unable to alter repos due to {0}".format(e))
            return False

        if len(enable_list) > 0:
            self.log.debug("Enabled the following repos: %s" %
                           (", ".join(enable_list)).replace('--enable=', ''))
        if len(disable_list) > 0:
            self.log.debug("Disabled the following repos: %s" %
                           (", ".join(disable_list)).replace('--disable=', ''))
        return True
