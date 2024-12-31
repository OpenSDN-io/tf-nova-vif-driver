#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

import setuptools


def requirements(filename):
    with open(filename) as f:
        lines = f.read().splitlines()
    return lines


setuptools.setup(
    name='nova_contrail_vif',
    version='0.1.dev0',

    author="OpenContrail",
    author_email="dev@lists.opencontrail.org",
    license="Apache Software License",
    url="http://www.opencontrail.org/",
    long_description="OpenContrail Nova VIF driver",

    package_data={'': ['*.html', '*.css', '*.xml']},
    packages=setuptools.find_packages(),
    install_requires=requirements('requirements.txt'),
    entry_points={
      'os_vif': [
        'contrail_vrouter = vif_plug_contrail_vrouter.vrouter:VrouterPlugin',
        'vrouter = vif_plug_vrouter.vrouter:VrouterPlugin'
      ]
    },
    zip_safe=False,
)
