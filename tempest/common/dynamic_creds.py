# Copyright 2013 IBM Corp.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import netaddr
from oslo_log import log as logging
import six
from tempest_lib import exceptions as lib_exc

from tempest import clients
from tempest.common import cred_client
from tempest.common import cred_provider
from tempest.common.utils import data_utils
from tempest import config
from tempest import exceptions

CONF = config.CONF
LOG = logging.getLogger(__name__)


class DynamicCredentialProvider(cred_provider.CredentialProvider):

    def __init__(self, identity_version=None, name=None,
                 network_resources=None):
        super(DynamicCredentialProvider, self).__init__(
            identity_version, name, network_resources)
        self.network_resources = network_resources
        self._creds = {}
        self.ports = []
        self.default_admin_creds = cred_provider.get_configured_credentials(
            'identity_admin', fill_in=True,
            identity_version=self.identity_version)
        (self.identity_admin_client, self.network_admin_client,
         self.networks_admin_client) = self._get_admin_clients()
        # Domain where dynamic credentials are provisioned (v3 only).
        # Use that of the admin account is None is configured.
        self.creds_domain_name = None
        if self.identity_version == 'v3':
            self.creds_domain_name = (
                self.default_admin_creds.project_domain_name or
                CONF.auth.default_credentials_domain_name)
        self.creds_client = cred_client.get_creds_client(
            self.identity_admin_client, self.creds_domain_name)

    def _get_admin_clients(self):
        """
        Returns a tuple with instances of the following admin clients (in this
        order):
            identity
            network
        """
        os = clients.Manager(self.default_admin_creds)
        if self.identity_version == 'v2':
            return os.identity_client, os.network_client, os.networks_client
        else:
            return os.identity_v3_client, os.network_client, os.networks_client

    def _create_creds(self, suffix="", admin=False, roles=None):
        """Create random credentials under the following schema.

        If the name contains a '.' is the full class path of something, and
        we don't really care. If it isn't, it's probably a meaningful name,
        so use it.

        For logging purposes, -user and -tenant are long and redundant,
        don't use them. The user# will be sufficient to figure it out.
        """
        if '.' in self.name:
            root = ""
        else:
            root = self.name

        project_name = data_utils.rand_name(root) + suffix
        project_desc = project_name + "-desc"
        project = self.creds_client.create_project(
            name=project_name, description=project_desc)

        username = data_utils.rand_name(root) + suffix
        user_password = data_utils.rand_password()
        email = data_utils.rand_name(root) + suffix + "@example.com"
        user = self.creds_client.create_user(
            username, user_password, project, email)
        if 'user' in user:
            user = user['user']
        role_assigned = False
        if admin:
            self.creds_client.assign_user_role(user, project,
                                               CONF.identity.admin_role)
            role_assigned = True
        # Add roles specified in config file
        for conf_role in CONF.auth.tempest_roles:
            self.creds_client.assign_user_role(user, project, conf_role)
            role_assigned = True
        # Add roles requested by caller
        if roles:
            for role in roles:
                self.creds_client.assign_user_role(user, project, role)
                role_assigned = True
        # NOTE(mtreinish) For a user to have access to a project with v3 auth
        # it must beassigned a role on the project. So we need to ensure that
        # our newly created user has a role on the newly created project.
        if self.identity_version == 'v3' and not role_assigned:
            self.creds_client.create_user_role('Member')
            self.creds_client.assign_user_role(user, project, 'Member')

        creds = self.creds_client.get_credentials(user, project, user_password)
        return cred_provider.TestResources(creds)

    def _create_network_resources(self, tenant_id):
        network = None
        subnet = None
        router = None
        # Make sure settings
        if self.network_resources:
            if self.network_resources['router']:
                if (not self.network_resources['subnet'] or
                    not self.network_resources['network']):
                    raise exceptions.InvalidConfiguration(
                        'A router requires a subnet and network')
            elif self.network_resources['subnet']:
                if not self.network_resources['network']:
                    raise exceptions.InvalidConfiguration(
                        'A subnet requires a network')
            elif self.network_resources['dhcp']:
                raise exceptions.InvalidConfiguration('DHCP requires a subnet')

        data_utils.rand_name_root = data_utils.rand_name(self.name)
        if not self.network_resources or self.network_resources['network']:
            network_name = data_utils.rand_name_root + "-network"
            network = self._create_network(network_name, tenant_id)
        try:
            if not self.network_resources or self.network_resources['subnet']:
                subnet_name = data_utils.rand_name_root + "-subnet"
                subnet = self._create_subnet(subnet_name, tenant_id,
                                             network['id'])
            if not self.network_resources or self.network_resources['router']:
                router_name = data_utils.rand_name_root + "-router"
                router = self._create_router(router_name, tenant_id)
                self._add_router_interface(router['id'], subnet['id'])
        except Exception:
            if router:
                self._clear_isolated_router(router['id'], router['name'])
            if subnet:
                self._clear_isolated_subnet(subnet['id'], subnet['name'])
            if network:
                self._clear_isolated_network(network['id'], network['name'])
            raise
        return network, subnet, router

    def _create_network(self, name, tenant_id):
        resp_body = self.networks_admin_client.create_network(
            name=name, tenant_id=tenant_id)
        return resp_body['network']

    def _create_subnet(self, subnet_name, tenant_id, network_id):
        base_cidr = netaddr.IPNetwork(CONF.network.tenant_network_cidr)
        mask_bits = CONF.network.tenant_network_mask_bits
        for subnet_cidr in base_cidr.subnet(mask_bits):
            try:
                if self.network_resources:
                    resp_body = self.network_admin_client.\
                        create_subnet(
                            network_id=network_id, cidr=str(subnet_cidr),
                            name=subnet_name,
                            tenant_id=tenant_id,
                            enable_dhcp=self.network_resources['dhcp'],
                            ip_version=4)
                else:
                    resp_body = self.network_admin_client.\
                        create_subnet(network_id=network_id,
                                      cidr=str(subnet_cidr),
                                      name=subnet_name,
                                      tenant_id=tenant_id,
                                      ip_version=4)
                break
            except lib_exc.BadRequest as e:
                if 'overlaps with another subnet' not in str(e):
                    raise
        else:
            message = 'Available CIDR for subnet creation could not be found'
            raise Exception(message)
        return resp_body['subnet']

    def _create_router(self, router_name, tenant_id):
        external_net_id = dict(
            network_id=CONF.network.public_network_id)
        resp_body = self.network_admin_client.create_router(
            router_name,
            external_gateway_info=external_net_id,
            tenant_id=tenant_id)
        return resp_body['router']

    def _add_router_interface(self, router_id, subnet_id):
        self.network_admin_client.add_router_interface_with_subnet_id(
            router_id, subnet_id)

    def get_credentials(self, credential_type):
        if self._creds.get(str(credential_type)):
            credentials = self._creds[str(credential_type)]
        else:
            if credential_type in ['primary', 'alt', 'admin']:
                is_admin = (credential_type == 'admin')
                credentials = self._create_creds(admin=is_admin)
            else:
                credentials = self._create_creds(roles=credential_type)
            self._creds[str(credential_type)] = credentials
            # Maintained until tests are ported
            LOG.info("Acquired dynamic creds:\n credentials: %s"
                     % credentials)
            if (CONF.service_available.neutron and
                not CONF.baremetal.driver_enabled and
                CONF.auth.create_isolated_networks):
                network, subnet, router = self._create_network_resources(
                    credentials.tenant_id)
                credentials.set_resources(network=network, subnet=subnet,
                                          router=router)
                LOG.info("Created isolated network resources for : \n"
                         + " credentials: %s" % credentials)
        return credentials

    def get_primary_creds(self):
        return self.get_credentials('primary')

    def get_admin_creds(self):
        return self.get_credentials('admin')

    def get_alt_creds(self):
        return self.get_credentials('alt')

    def get_creds_by_roles(self, roles, force_new=False):
        roles = list(set(roles))
        # The roles list as a str will become the index as the dict key for
        # the created credentials set in the dynamic_creds dict.
        exist_creds = self._creds.get(str(roles))
        # If force_new flag is True 2 cred sets with the same roles are needed
        # handle this by creating a separate index for old one to store it
        # separately for cleanup
        if exist_creds and force_new:
            new_index = str(roles) + '-' + str(len(self._creds))
            self._creds[new_index] = exist_creds
            del self._creds[str(roles)]
        return self.get_credentials(roles)

    def _clear_isolated_router(self, router_id, router_name):
        net_client = self.network_admin_client
        try:
            net_client.delete_router(router_id)
        except lib_exc.NotFound:
            LOG.warn('router with name: %s not found for delete' %
                     router_name)

    def _clear_isolated_subnet(self, subnet_id, subnet_name):
        net_client = self.network_admin_client
        try:
            net_client.delete_subnet(subnet_id)
        except lib_exc.NotFound:
            LOG.warn('subnet with name: %s not found for delete' %
                     subnet_name)

    def _clear_isolated_network(self, network_id, network_name):
        net_client = self.networks_admin_client
        try:
            net_client.delete_network(network_id)
        except lib_exc.NotFound:
            LOG.warn('network with name: %s not found for delete' %
                     network_name)

    def _cleanup_default_secgroup(self, tenant):
        net_client = self.network_admin_client
        resp_body = net_client.list_security_groups(tenant_id=tenant,
                                                    name="default")
        secgroups_to_delete = resp_body['security_groups']
        for secgroup in secgroups_to_delete:
            try:
                net_client.delete_security_group(secgroup['id'])
            except lib_exc.NotFound:
                LOG.warn('Security group %s, id %s not found for clean-up' %
                         (secgroup['name'], secgroup['id']))

    def _clear_isolated_net_resources(self):
        net_client = self.network_admin_client
        for cred in self._creds:
            creds = self._creds.get(cred)
            if (not creds or not any([creds.router, creds.network,
                                      creds.subnet])):
                continue
            LOG.debug("Clearing network: %(network)s, "
                      "subnet: %(subnet)s, router: %(router)s",
                      {'network': creds.network, 'subnet': creds.subnet,
                       'router': creds.router})
            if (not self.network_resources or
                    (self.network_resources.get('router') and creds.subnet)):
                try:
                    net_client.remove_router_interface_with_subnet_id(
                        creds.router['id'], creds.subnet['id'])
                except lib_exc.NotFound:
                    LOG.warn('router with name: %s not found for delete' %
                             creds.router['name'])
                self._clear_isolated_router(creds.router['id'],
                                            creds.router['name'])
            if (not self.network_resources or
                self.network_resources.get('subnet')):
                self._clear_isolated_subnet(creds.subnet['id'],
                                            creds.subnet['name'])
            if (not self.network_resources or
                self.network_resources.get('network')):
                self._clear_isolated_network(creds.network['id'],
                                             creds.network['name'])

    def clear_creds(self):
        if not self._creds:
            return
        self._clear_isolated_net_resources()
        for creds in six.itervalues(self._creds):
            try:
                self.creds_client.delete_user(creds.user_id)
            except lib_exc.NotFound:
                LOG.warn("user with name: %s not found for delete" %
                         creds.username)
            try:
                if CONF.service_available.neutron:
                    self._cleanup_default_secgroup(creds.tenant_id)
                self.creds_client.delete_project(creds.tenant_id)
            except lib_exc.NotFound:
                LOG.warn("tenant with name: %s not found for delete" %
                         creds.tenant_name)
        self._creds = {}

    def is_multi_user(self):
        return True

    def is_multi_tenant(self):
        return True

    def is_role_available(self, role):
        return True
