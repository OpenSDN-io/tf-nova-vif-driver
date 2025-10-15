#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

# -*- mode: python; -*-

env = DefaultEnvironment().Clone()

base_path = '#openstack/nova_contrail_vif/'

sdist_gen = env.Command(
    '/pip/nova_contrail_vif-0.1.dev0-py3-none-any.whl', 'setup.py',
    'cd ' + Dir(base_path).path + ' && ' + 'python3 setup.py bdist_wheel --dist-dir /pip')
env.Alias('install', sdist_gen)
