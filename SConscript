#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

env = DefaultEnvironment().Clone()

base_path = '#openstack/nova_contrail_vif/'

sources = ['setup.py', 'requirements.txt']
sources += env.AddPythonSources('nova_contrail_vif')
sources += env.AddPythonSources('vif_plug_contrail_vrouter')
sources += env.AddPythonSources('vif_plug_vrouter')

sdist_gen = env.Command(
    '/pip/nova_contrail_vif-0.1.dev0-py3-none-any.whl', sources,
    'cd ' + Dir(base_path).path + ' && ' + 'python3 setup.py bdist_wheel --dist-dir /pip')
env.Alias('install', sdist_gen)
