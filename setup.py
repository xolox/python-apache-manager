#!/usr/bin/env python

"""Setup script for the `apache-manager` package."""

# Author: Peter Odding <peter@peterodding.com>
# Last Change: October 4, 2015
# URL: https://apache-manager.readthedocs.org

# Standard library modules.
import codecs
import os
import re

# De-facto standard solution for Python packaging.
from setuptools import setup, find_packages

# Find the directory where the source distribution was unpacked.
source_directory = os.path.dirname(os.path.abspath(__file__))

# Find the current version.
module = os.path.join(source_directory, 'apache_manager', '__init__.py')
for line in open(module, 'r'):
    match = re.match(r'^__version__\s*=\s*["\']([^"\']+)["\']$', line)
    if match:
        version_string = match.group(1)
        break
else:
    raise Exception("Failed to extract version from %s!" % module)

# Fill in the long description (for the benefit of PyPI)
# with the contents of README.rst (rendered by GitHub).
readme_file = os.path.join(source_directory, 'README.rst')
with codecs.open(readme_file, 'r', 'utf-8') as handle:
    readme_text = handle.read()

setup(
    name='apache-manager',
    version=version_string,
    description="Monitor and control Apache web server workers from Python",
    long_description=readme_text,
    url='https://apache-manager.readthedocs.org',
    author='Peter Odding',
    author_email='peter@peterodding.com',
    packages=find_packages(),
    test_suite='apache_manager.tests',
    install_requires=[
        'beautifulsoup4 >= 4.3.2',
        'coloredlogs >= 1.0.1',
        'humanfriendly >= 1.31',
        'proc >= 0.2.2',
        'property-manager >= 1.0.1',
    ],
    tests_require=[
        'capturer >= 2.1',
    ],
    entry_points=dict(console_scripts=[
        'apache-manager = apache_manager.cli:main'
    ]),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python',
        'Topic :: Internet :: WWW/HTTP :: HTTP Servers',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: System :: Systems Administration',
    ])
