# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
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

import sys
import time
import gettext
import threading
import os.path as path

import eventlet
from oslo.config import cfg

from nova.network import linux_net

gettext.install('contrail_vif')


# Default to _ as this does not work in havana
try:
    from nova.openstack.common.gettextutils import _LE
except:
    _LE = _
from nova.openstack.common import log as logging
from nova.openstack.common import loopingcall
from nova.openstack.common import processutils
from nova.virt.libvirt import designer
# Support for JUNO - Phase 1
# JUNO release doesn't support libvirt_vif_driver configuration in nova.conf
# vif_driver is set to LibvirtGenericVIFDriver. plug/unplug/get_config api from
# this class doesn't support opencontrail. Till opencontrail vif_driver is
# upstreamed, overwrite the vif_driver with VRouterVIFDriver
# This code will be removed after OpenContrail vif driver is upstreamed
try:
    from nova.virt.libvirt.vif import LibvirtBaseVIFDriver as LibVirtVIFDriver
except ImportError:
    # JUNO doesn't have LibvirtBaseVIFDriver implementation. So inherit VRouterVIFDriver
    # from LibvirtGenericVIFDriver
    from nova.virt.libvirt.vif import LibvirtGenericVIFDriver as LibVirtVIFDriver

from contrail_vrouter_api.vrouter_api import ContrailVRouterApi

from thrift.Thrift import TApplicationException

LOG = logging.getLogger(__name__)


from nova.network.neutronv2.api import API
from nova.compute.manager import ComputeManager
from nova.compute import utils as compute_utils

orig_get_nw_info_for_instance = None
compute_mgr = None

CONF = cfg.CONF
contrail_vif_opts = [
    cfg.BoolOpt('use_userspace_vhost',
                default=False,
                help='Use qemu userspace-vhost for backing guest interfaces'),
    cfg.StrOpt('userspace_vhost_socket_dir',
               default='/var/run/vrouter',
               help='Directory for userspace vhost sockets'),
]
CONF.register_opts(contrail_vif_opts, 'contrail')


# MonkeyPatch the vif_driver with VRouterVIFDriver during restart of nova-compute
def patched_get_nw_info_for_instance(instance):
    if any(['nova-compute' in arg for arg in sys.argv]):
        if not isinstance(compute_mgr.driver.vif_driver, VRouterVIFDriver):
            compute_mgr.driver.vif_driver = \
                VRouterVIFDriver(compute_mgr.driver._get_connection)
    return orig_get_nw_info_for_instance(instance)

class ContrailNetworkAPI(API):
    def __init__(self):
        # MonkeyPatch the compute_utils.get_nw_info_for_instance with
        # patched_get_nw_info_for_instance to enable overwriting vif_driver
        global orig_get_nw_info_for_instance
        if orig_get_nw_info_for_instance is None:
            orig_get_nw_info_for_instance = compute_utils.get_nw_info_for_instance
            compute_utils.get_nw_info_for_instance = patched_get_nw_info_for_instance
        # Store the compute manager object to overwrite vif_driver
        import inspect
        global compute_mgr
        if any(['nova-compute' in arg for arg in sys.argv]):
            # patch only for nova-compute
            compute_mgr = inspect.stack()[2][0].f_locals.get('self')
            if not isinstance(compute_mgr, ComputeManager):
                compute_mgr = inspect.stack()[5][0].f_locals.get('self')
            if not isinstance(compute_mgr, ComputeManager):
                raise BadRequest("Can't get hold of compute manager")
        super(ContrailNetworkAPI, self).__init__()
    #end __init__

    def allocate_for_instance(self, *args, **kwargs):
        # Monkey patch the vif_driver if not already set
        if not isinstance(compute_mgr.driver.vif_driver, VRouterVIFDriver):
            compute_mgr.driver.vif_driver = \
                VRouterVIFDriver(compute_mgr.driver._get_connection)
        return super(ContrailNetworkAPI, self).allocate_for_instance(*args, **kwargs)
    #end

    def deallocate_for_instance(self, *args, **kwargs):
        # Monkey patch the vif_driver if not already set
        if not isinstance(compute_mgr.driver.vif_driver, VRouterVIFDriver):
            compute_mgr.driver.vif_driver = \
                VRouterVIFDriver(compute_mgr.driver._get_connection)
        return super(ContrailNetworkAPI, self).deallocate_for_instance(*args, **kwargs)
    #end
#end ContrailNetworkAPI

class VRouterVIFDriver(LibVirtVIFDriver):
    """VIF driver for VRouter when running Neutron."""

    PORT_TYPE = 'NovaVMPort'

    def __init__(self, get_connection):
        super(VRouterVIFDriver, self).__init__(get_connection)
        self._vrouter_semaphore = eventlet.semaphore.Semaphore()
        self._vrouter_client = ContrailVRouterApi(doconnect=True, semaphore=self._vrouter_semaphore)
        timer = loopingcall.FixedIntervalLoopingCall(self._keep_alive)
        timer.start(interval=2)

    def _keep_alive(self):
        self._vrouter_client.periodic_connection_check()

    @staticmethod
    def _get_br_name(dev):
        """Returns the bridge name for a tap device.
        This is lxc related stuff. To work around the fact, that libvirt does
        not support direct passthrough of devices to LXC."""
        return 'br%s' % dev[3:]

    def _create_bridge(self, dev, instance):
        """Creating a bridge and returning its name"""
        br_name = self._get_br_name(dev)

        try:
            linux_net.LinuxBridgeInterfaceDriver.ensure_bridge(br_name, dev)
            linux_net._execute('ip', 'link', 'set', br_name, 'promisc', 'on',
                               run_as_root=True)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while plugging vif"), instance=instance)

        return br_name

    def get_config(self, instance, vif, image_meta, inst_type, virt_type=None):
        try:
            conf = super(VRouterVIFDriver, self).get_config(instance, vif,
                                                        image_meta, inst_type)
        except TypeError:
            conf = super(VRouterVIFDriver, self).get_base_config(instance, vif,
                                             image_meta, inst_type, virt_type)
        dev = self.get_vif_devname(vif)
        if not virt_type:
            try:
                virt_type = cfg.CONF.libvirt.virt_type
            except cfg.NoSuchOptError:
                virt_type = cfg.CONF.libvirt_type

        if virt_type == 'lxc':
            # for lxc we need to pass a bridge to libvirt
            br_name = self._get_br_name(dev)
            designer.set_vif_host_backend_bridge_config(conf, br_name)
        else:
            if CONF.contrail.use_userspace_vhost:
                dev = path.join(CONF.contrail.userspace_vhost_socket_dir,
                                'uvh_vif_' + dev)
                designer.set_vif_host_backend_vhostuser_config(conf, 'client',
                        dev)
            else:
                designer.set_vif_host_backend_ethernet_config(conf, dev)
        designer.set_vif_bandwidth_config(conf, inst_type)

        return conf

    def plug(self, instance, vif):
        try:
            dev = self.get_vif_devname(vif)

            try:
                if not CONF.contrail.use_userspace_vhost:
                    linux_net.create_tap_dev(dev)
            except processutils.ProcessExecutionError:
                LOG.exception(_LE("Failed while plugging vif"), instance=instance)

            try:
                virt_type = cfg.CONF.libvirt.virt_type
            except cfg.NoSuchOptError:
                virt_type = cfg.CONF.libvirt_type

            if virt_type == 'lxc':
                dev = self._create_bridge(dev, instance)

            ipv4_address = '0.0.0.0'
            ipv6_address = None
            subnets = vif['network']['subnets']
            for subnet in subnets:
                ips = subnet['ips'][0]
                if (ips['version'] == 4):
                    if ips['address'] is not None:
                        ipv4_address = ips['address']
                if (ips['version'] == 6):
                    if ips['address'] is not None:
                        ipv6_address = ips['address']

            kwargs = {
                'ip_address': ipv4_address,
                'vn_id': vif['network']['id'],
                'display_name': instance['display_name'],
                'hostname': instance['hostname'],
                'host': instance['host'],
                'vm_project_id': instance['project_id'],
                'port_type': self.PORT_TYPE,
                'ip6_address': ipv6_address,
            }
            try:
                result = self._vrouter_client.add_port(instance['uuid'],
                                                       vif['id'],
                                                       dev,
                                                       vif['address'],
                                                       **kwargs)
                if not result:
                    LOG.exception(_LE("Failed while plugging vif"),
                                  instance=instance)
            except TApplicationException:
                LOG.exception(_LE("Failed while plugging vif"), instance=instance)

        except Exception as e:
            from pprint import pformat
            LOG.error(_("Error in plug: %s locals: %s instance %s"
                       %(str(e), pformat(locals()),
                         pformat(instance) if isinstance(instance, dict) else pformat(instance.__dict__))))

    def unplug(self, instance, vif):
        try:
            dev = self.get_vif_devname(vif)

            if isinstance(instance, dict):
                task_state = instance['task_state']
            else:
                task_state = instance._task_state

            try:
                self._vrouter_client.delete_port(vif['id'])
                if task_state == 'rebuilding':
                    self.delete_device(dev)
                else:
                    # delegate the deletion of tap device to a deffered thread
                    worker_thread = threading.Thread(
                        target=self.delete_device,
                        name='contrailvif',
                        args=(dev,), kwargs={'timeout': 2})
                    worker_thread.start()
            except (TApplicationException, processutils.ProcessExecutionError,
                    RuntimeError):
                LOG.exception(_LE("Failed while unplugging vif"),
                              instance=instance)
        except Exception as e:
            from pprint import pformat
            LOG.error(_("Error in unplug: %s locals: %s instance %s"
                       %(str(e), pformat(locals()),
                         pformat(instance) if isinstance(instance, dict) else pformat(instance.__dict__))))

    def delete_device(self, dev, timeout=None):
        if timeout is not None:
            time.sleep(timeout)
        LOG.debug(dev)

        try:
            virt_type = cfg.CONF.libvirt.virt_type
        except cfg.NoSuchOptError:
            virt_type = cfg.CONF.libvirt_type

        if virt_type == 'lxc':
            linux_net.LinuxBridgeInterfaceDriver.remove_bridge(
                    self._get_br_name(dev))
        if not CONF.contrail.use_userspace_vhost:
            linux_net.delete_net_dev(dev)
