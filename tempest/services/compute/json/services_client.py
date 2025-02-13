# Copyright 2013 NEC Corporation
# Copyright 2013 IBM Corp.
# All Rights Reserved.
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

from oslo_serialization import jsonutils as json
from six.moves.urllib import parse as urllib

from tempest.api_schema.response.compute.v2_1 import services as schema
from tempest.common import service_client


class ServicesClient(service_client.ServiceClient):

    def list_services(self, **params):
        url = 'os-services'
        if params:
            url += '?%s' % urllib.urlencode(params)

        resp, body = self.get(url)
        body = json.loads(body)
        self.validate_response(schema.list_services, resp, body)
        return service_client.ResponseBody(resp, body)

    def enable_service(self, host_name, binary):
        """
        Enable service on a host
        host_name: Name of host
        binary: Service binary
        """
        post_body = json.dumps({'binary': binary, 'host': host_name})
        resp, body = self.put('os-services/enable', post_body)
        body = json.loads(body)
        self.validate_response(schema.enable_disable_service, resp, body)
        return service_client.ResponseBody(resp, body)

    def disable_service(self, host_name, binary):
        """
        Disable service on a host
        host_name: Name of host
        binary: Service binary
        """
        post_body = json.dumps({'binary': binary, 'host': host_name})
        resp, body = self.put('os-services/disable', post_body)
        body = json.loads(body)
        self.validate_response(schema.enable_disable_service, resp, body)
        return service_client.ResponseBody(resp, body)
